// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {FixedPointMath} from "./libraries/FixedPointMath.sol";

/// RSK Bridge — the precompiled contract that vets Bitcoin headers on-chain.
interface IBridge {
    function getBtcBlockchainBestChainHeight()
        external
        view
        returns (uint256);
    function getBtcBlockchainBlockHeaderByHeight(uint256 index)
        external
        view
        returns (bytes memory);
}

/// @title DBTCPrice
/// @notice A pure-difficulty BTC/DBTC price oracle for Rootstock (RSK).
///
/// Implements the repo's model directly and nothing more. Bitcoin difficulty
/// is read on-chain from the RSK Bridge's view of Bitcoin block headers and is
/// turned into a price with the fitted power law applied to *smoothed*
/// difficulty, matching `lending.py` / `backtest.py`:
///
///     DBTC per BTC = (D_s / D_s0)^b            BTC per DBTC = 1 / (D_s / D_s0)^b
///
/// where `D_s` is the mean difficulty over the trailing `windowSeconds`
/// (default 365d, the flagship smoothing in `lending.py`) and `b` (~0.69) is
/// the OLS-fitted exponent (see README).
///
/// Difficulty only changes once per 2016-block period, so the rolling mean is
/// updated once per period, not per block. Because `target = MaxTarget /
/// difficulty`, i.e. `difficulty = MaxTarget / target`, the ratio of two window
/// means cancels `MaxTarget`, so the contract only ever needs the per-epoch
/// compact `nBits` targets. To faithfully reproduce the calendar-day mean used
/// in Python (each day weighted equally), epochs are weighted by their *actual
/// duration*, taken from the block timestamp of each epoch's first header, with
/// the boundary epoch prorated and the still-open newest epoch excluded until
/// the next period. A scan reproduces `rolling(365).mean()` to well under 1%.
///
/// All price results are returned as signed 64.64 fixed point (value * 2**64).
contract DBTCPrice {
    uint256 internal constant RETARGET = 2016; // blocks per difficulty period
    uint256 internal constant INV_SCALE = 0x1 << 224; // 2^224, scale of stored 1/target
    uint256 internal constant DAY = 86400;

    /// Configurable; set to the RSK Bridge address in deployment.
    IBridge public immutable bridge;
    /// Smoothing window in seconds (0 at deploy → 365 days).
    uint256 public immutable windowSeconds;

    // ---- rolling smoothed current mean (D_s / t) ----
    // Ring of the last `cap` per-epoch entries; epoch index k (global) is
    // stored at `k % cap`. Each word packs (start << 224) | inv, where
    // inv = 2^224 / target. Scan iterates newest → oldest until the window
    // start, so ring size only needs to cover the max epochs in a window.
    uint256 public immutable cap;
    uint256[] internal epochs;      // length == cap, zero-initialised
    uint256 public totalEpochs;     // pushes so far (newest epoch index + 1)
    uint256 public currentTarget;   // nBits of the newest (open) epoch read
    uint256 public currentHeight;   // first block height of the newest epoch
    uint256 public currentTime;     // block timestamp of the newest epoch start (ref)
    uint256 public reads;           // bridge reads so far (cache-hit metric)

    // ---- anchor (D_s0), frozen once at mint ----
    uint256 public anchorArea;    // Σ inv×seconds over the anchor window
    uint256 public anchorSpan;    // seconds covered at the anchor (≤ windowSeconds)
    uint256 public anchorTarget;  // nBits of the epoch that anchored
    uint256 public anchorHeight;  // first block height of the anchoring epoch

    // ---- precomputed logs / ratio (constant between epoch changes) ----
    int256 public anchorLog2;   // log2(anchorArea/anchorSpan), set in anchor()
    int256 public currentLog2;  // log2(currentArea/currentSpan), set in refresh()
    int256 public currentRatio; // (D_s/D_s0)^b, recomputed once per epoch

    /// OLS exponent `b` (≈ 0.69), fixed at deployment in 64.64 form.
    int256 public immutable exponent;

    event Anchored(uint256 target, uint256 height);
    event Retargeted(uint256 target, uint256 height, uint256 epoch);

    error ZeroNBits();

    /// @param bridge_        RSK Bridge address.
    /// @param exponent_      fitted `b` as 64.64 (e.g. 0.69 * 2**64).
    /// @param windowSeconds_ smoothing window; 0 → 365 * DAY (≈26 periods).
    constructor(address bridge_, int256 exponent_, uint256 windowSeconds_) {
        bridge = IBridge(bridge_);
        exponent = exponent_;
        windowSeconds = windowSeconds_ == 0 ? 365 * DAY : windowSeconds_;
        // 2 days per epoch is a safe floor for ring sizing; generous margin.
        cap = windowSeconds / (2 * DAY) + 8;
        epochs = new uint256[](cap);
    }

    // ------------------------------------------------------------------ read
    /// Read target (nBits) and block time of a given chain height.
    /// Bitcoin header layout (80 bytes): version(4) prev(32) merkle(32)
    /// time(4)=[68..71] nBits(4)=[72..75] nonce(4)=[76..79], all LE.
    function readTargetAndTime(uint256 h)
        public
        view
        returns (uint256 compactNBits, uint256 time)
    {
        bytes memory header = bridge.getBtcBlockchainBlockHeaderByHeight(h);
        compactNBits = uint256(uint8(header[72]))
            | (uint256(uint8(header[73])) << 8)
            | (uint256(uint8(header[74])) << 16)
            | (uint256(uint8(header[75])) << 24);
        time = uint256(uint8(header[68]))
            | (uint256(uint8(header[69])) << 8)
            | (uint256(uint8(header[70])) << 16)
            | (uint256(uint8(header[71])) << 24);
    }

    /// @param packed the stored (start << 224) | inv word.
    function _startOf(uint256 packed) internal pure returns (uint256) {
        return packed >> 224;
    }

    function _invOf(uint256 packed) internal pure returns (uint256) {
        return packed & ((0x1 << 224) - 1);
    }

    /// Sliding-window weighted sum (inv×seconds), the analogue of Python's
    /// `difficulty.rolling(W).mean()` evaluated at the newest epoch start.
    /// Newest (open) epoch is excluded until its successor arrives.
    function _scan() internal view returns (uint256 area, uint256 span) {
        if (totalEpochs < 2) return (0, 0);
        uint256 ref = currentTime;
        uint256 lo = ref > windowSeconds ? ref - windowSeconds : 0;
        // iterate closed epochs from newest-1 down; break past the window start
        for (uint256 k = totalEpochs - 1; k > 0; --k) {
            uint256 e = epochs[(k - 1) % cap];
            if (e == 0) continue; // slot never written
            uint256 a = _startOf(e);          // this epoch's start
            uint256 nb = _startOf(epochs[k % cap]); // next epoch's start == this end
            if (nb <= lo) break;              // fully before window
            uint256 s = a > lo ? a : lo;
            uint256 en = nb < ref ? nb : ref;
            if (en > s) {
                uint256 ov = en - s;
                area += _invOf(e) * ov;
                span += ov;
            }
        }
    }

    /// Smoothed difficulty ratio (D_s/D_s0)^b as 64.64, cached per epoch.
    /// Public for inspection; called by price getters.
    function difficultyRatio() public view returns (int256) {
        int256 r = currentRatio;
        if (r == 0) revert ZeroNBits();
        return r;
    }

    /// DBTC per BTC = (D_s/D_s0)^b, 64.64.
    function dbtcPerBtc() external view returns (int256) {
        return difficultyRatio();
    }

    /// BTC per DBTC = 1 / (D_s/D_s0)^b, 64.64.
    function btcPerDbtc() external view returns (int256) {
        int256 r = difficultyRatio();
        require(r != 0, "zero ratio");
        return (FixedPointMath.SCALE * FixedPointMath.SCALE) / r;
    }

    /// Snapshot of the current smoothed difficulty: (Σ inv·seconds, seconds).
    function smoothedState() external view returns (uint256 area, uint256 span) {
        return _scan();
    }

    // -------------------------------------------------------------- mutating
    /// Set the anchor to the current smoothed difficulty (D_s0).
    function anchor() external {
        refresh();
        (anchorArea, anchorSpan) = _scan();
        if (anchorArea == 0) revert ZeroNBits();
        anchorLog2 = FixedPointMath.log2(anchorArea) - FixedPointMath.log2(anchorSpan);
        anchorTarget = currentTarget;
        anchorHeight = currentHeight;
        // Recompute the ratio against the new anchor (= exp2(0) = 1 now).
        currentLog2 = anchorLog2;
        currentRatio = FixedPointMath.exp2(
            FixedPointMath.mul(exponent, anchorLog2 - currentLog2)
        );
        emit Anchored(anchorTarget, anchorHeight);
    }

    /// Re-read the bridge only if best height has advanced into a new epoch.
    function refresh() public {
        uint256 best = bridge.getBtcBlockchainBestChainHeight();
        uint256 epoch = best / RETARGET;
        // No progress past a retarget boundary -> cached value is current.
        if (currentTarget != 0 && epoch <= totalEpochs) return;

        // The first block of the epoch carries that epoch's difficulty.
        uint256 h = (epoch == 0 ? best : epoch * RETARGET);
        (uint256 t, uint256 time) = readTargetAndTime(h);
        if (t == 0) revert ZeroNBits();
        currentTarget = t;
        currentHeight = h;
        currentTime = time;
        uint256 inv = INV_SCALE / t;
        // 2^128 guard keeps Σ(inv·seconds) far clear of uint256 (never hit
        // in practice: modern target ~2^176 → inv ~2^48).
        if (inv >= (0x1 << 128)) revert ZeroNBits();
        epochs[totalEpochs % cap] = (time << 224) | inv;
        totalEpochs++;

        (uint256 area, uint256 span) = _scan();
        if (area != 0 && span != 0) {
            currentLog2 = FixedPointMath.log2(area) - FixedPointMath.log2(span);
            if (anchorLog2 != 0) {
                currentRatio = FixedPointMath.exp2(
                    FixedPointMath.mul(exponent, anchorLog2 - currentLog2)
                );
            }
        }
        reads++;
        emit Retargeted(t, h, epoch);
    }
}