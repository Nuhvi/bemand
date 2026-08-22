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
/// where `D_s` is the mean difficulty over the trailing `window` difficulty
/// periods (default 26, the flagship smoothing in `lending.py`), `D_s0` is
/// that window mean frozen at the anchor, and `b` (~0.69) is the OLS-fitted
/// exponent (see README).
///
/// Difficulty only changes once per 2016-block period, so the smoothing
/// window advances once per period, not per block. The window is a simple ring
/// of `window` per-epoch difficulties, each weighted equally (no block
/// timestamps needed). Since `difficulty = MaxTarget / target`, a per-period
/// effective difficulty is stored as `2^224 / target` (the `MaxTarget / 2^224`
/// constant cancels in the ratio), so the contract only ever reads the compact
/// `nBits` field of each period's first header. The price ratio is
///     D_s/D_s0 = Σ current window / Σ anchor window
/// and is recomputed once per period via log2/exp2 — read calls are pure
/// storage.
///
/// All price results are returned as signed 64.64 fixed point (value * 2**64).
contract DBTCPrice {
    uint256 internal constant RETARGET = 2016; // blocks per difficulty period
    uint256 internal constant D_SCALE = 0x1 << 224; // scale of stored per-period difficulty

    /// Configurable; set to the RSK Bridge address in deployment.
    IBridge public immutable bridge;
    /// Number of difficulty periods in the smoothing window (0 at deploy → 26).
    uint256 public immutable window;

    // ---- rolling window (D_s / t) ----
    uint256[] internal ring;   // per-epoch 2^224/target, ring of `window`
    uint256 internal head;     // index of the oldest entry once the ring is full
    uint256 internal filled;   // entries stored so far (<= window)
    uint256 public currentSum; // Σ over the window of per-period difficulty
    uint256 public currentTarget; // nBits of the newest epoch read
    uint256 public currentHeight; // first block height of the newest epoch
    uint256 public currentEpoch;  // currentHeight / RETARGET
    uint256 public reads;         // bridge reads so far (cache-hit metric)

    // ---- anchor (D_s0), frozen once at mint ----
    uint256 public anchorSum;    // ring sum frozen at anchor()
    uint256 public anchorTarget; // nBits of the epoch that anchored
    uint256 public anchorHeight; // first block height of the anchoring epoch

    // ---- precomputed logs / ratio (constant between epoch changes) ----
    int256 public anchorLog2;   // log2(anchorSum), set in anchor()
    int256 public currentLog2;  // log2(currentSum), set in refresh()
    int256 public currentRatio; // (D_s/D_s0)^b, recomputed once per epoch

    /// OLS exponent `b` (≈ 0.69), fixed at deployment in 64.64 form.
    int256 public immutable exponent;

    event Anchored(uint256 target, uint256 height);
    event Retargeted(uint256 target, uint256 height, uint256 epoch);

    error ZeroNBits();

    /// @param bridge_   RSK Bridge address.
    /// @param exponent_ fitted `b` as 64.64 (e.g. 0.69 * 2**64).
    /// @param window_   smoothing window in difficulty periods; 0 → 26.
    constructor(address bridge_, int256 exponent_, uint256 window_) {
        bridge = IBridge(bridge_);
        exponent = exponent_;
        window = window_ == 0 ? 26 : window_;
    }

    // ------------------------------------------------------------------ read
    /// Read the compact Bitcoin target (nBits) at a given chain height.
    function readTargetAtHeight(uint256 h)
        public
        view
        returns (uint256 compactNBits)
    {
        bytes memory header = bridge.getBtcBlockchainBlockHeaderByHeight(h);
        // nBits occupies header bytes 72..75, serialised little-endian as uint32.
        compactNBits = uint256(uint8(header[72]))
            | (uint256(uint8(header[73])) << 8)
            | (uint256(uint8(header[74])) << 16)
            | (uint256(uint8(header[75])) << 24);
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

    // -------------------------------------------------------------- mutating
    /// Roll one period's target `t` into the ring and refresh the cached ratio.
    function _pushEpoch(uint256 t) internal {
        uint256 d = D_SCALE / t; // effective difficulty of this period (~ target^-1)
        if (filled < window) {
            ring.push(d);
            filled++;
            currentSum += d;
        } else {
            currentSum -= ring[head];
            ring[head] = d;
            currentSum += d;
            head = (head + 1) % window;
        }
        currentLog2 = FixedPointMath.log2(currentSum);
        // Keep the price constant until the contract is anchored.
        if (anchorLog2 != 0) {
            currentRatio = FixedPointMath.exp2(
                FixedPointMath.mul(exponent, anchorLog2 - currentLog2)
            );
        }
    }

    /// Set the anchor to the current smoothed difficulty (D_s0).
    function anchor() external {
        refresh();
        anchorSum = currentSum;
        anchorLog2 = currentLog2;
        anchorTarget = currentTarget;
        anchorHeight = currentHeight;
        // Recompute the ratio against the new anchor (= exp2(0) = 1 now).
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
        if (currentTarget != 0 && epoch <= currentEpoch) return;

        // The first block of the epoch carries that epoch's difficulty.
        uint256 h = (epoch == 0 ? best : epoch * RETARGET);
        uint256 t = readTargetAtHeight(h);
        if (t == 0) revert ZeroNBits();
        currentTarget = t;
        currentHeight = h;
        currentEpoch = epoch;
        _pushEpoch(t);
        reads++;
        emit Retargeted(t, h, epoch);
    }
}