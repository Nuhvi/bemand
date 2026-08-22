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
/// where `D_s` is the mean difficulty over the trailing `window * 2016` blocks
/// (default `window` = 52 ≈ 2 years), `D_s0` is that mean frozen at the
/// anchor, and `b` (≈0.64) is the OLS-fitted exponent (see README).
///
/// The window is counted **in blocks**: every block contributes the difficulty
/// of the period it belongs to. Difficulty only changes once per 2016-block
/// period, so within a period the window advances one block at a time and the
/// mean drifts *smoothly* across a retarget instead of jumping. The 2016-block
/// groups collapse: for a given `best` height the window's weighted sum needs,
/// at most, `window + 1` period difficulties (one partial period at each end).
/// Only each period's first-header compact `nBits` is read, and only on
/// `refresh()` when a new epoch begins — read calls just scan the stored ring
/// plus the bridge's best height (one light call).
///
/// Since `difficulty = MaxTarget / target`, a period's effective difficulty is
/// `2^224 / target` (MaxTarget cancels in the ratio that forms the price), so
/// the contract stores per-period `(2^224 / target)` terms.
///
/// All price results are returned as signed 64.64 fixed point (value * 2**64).
contract DBTCPrice {
    uint256 internal constant RETARGET = 2016; // blocks per difficulty period
    uint256 internal constant D_SCALE = 0x1 << 224; // scale of stored per-period difficulty

    /// Configurable; set to the RSK Bridge address in deployment.
    IBridge public immutable bridge;
    /// Number of difficulty periods in the (block) smoothing window; 0 → 52.
    uint256 public immutable window;
    /// Window length in blocks == window * 2016.
    uint256 public immutable windowBlocks;
    /// Ring size: need the newest `window` + 1 period values to cover the edge.
    uint256 public immutable cap;

    // ---- per-period ring (most recent period at index currentPeriod) ----
    // Slot `k % cap` holds the effective difficulty (`2^224 / target`) of the
    // period whose first block is at height `k * 2016`.
    uint256[] internal ring;
    uint256 public totalPeriods;    // periods observed so far (== newest index + 1)
    uint256 public currentTarget;   // nBits of the newest period seen

    // ---- anchor (D_s0), frozen once at mint ----
    int256 public anchorWeight; // log2(anchorArea) - log2(anchorBlocks)

    /// OLS exponent `b` (≈ 0.64), fixed at deployment in 64.64 form.
    int256 public immutable exponent;

    event Anchored(uint256 height);
    event Retargeted(uint256 target, uint256 period, uint256 best);

    error ZeroNBits();
    error NotAnchored();

    /// @param bridge_   RSK Bridge address.
    /// @param exponent_ fitted `b` as 64.64 (e.g. 0.64 * 2**64).
    /// @param window_   smoothing window in difficulty periods; 0 → 52 (≈2y).
    constructor(address bridge_, int256 exponent_, uint256 window_) {
        bridge = IBridge(bridge_);
        exponent = exponent_;
        window = window_ == 0 ? 52 : window_;
        windowBlocks = window * RETARGET;
        cap = window + 2;
        ring = new uint256[](cap);
    }

    // ------------------------------------------------------------------ read
    /// Read the compact target (nBits) of the first block of the `epoch`-th
    /// period. Header layout: nBits[72..75] (LE uint32).
    function readNBitsAtHeight(uint256 h)
        public
        view
        returns (uint256 compactNBits)
    {
        bytes memory header = bridge.getBtcBlockchainBlockHeaderByHeight(h);
        compactNBits = uint256(uint8(header[72]))
            | (uint256(uint8(header[73])) << 8)
            | (uint256(uint8(header[74])) << 16)
            | (uint256(uint8(header[75])) << 24);
    }

    /// Current smoothed difficulty sum `D_s` as (weightedSum, blockCount) over
    /// the trailing `min(best+1, windowBlocks)` blocks, with period difficulties
    /// from the ring. `best` is the current Bitcoin best chain height.
    function _scanBlocks(uint256 best)
        internal
        view
        returns (uint256 weighted, uint256 blockCount)
    {
        if (totalPeriods == 0) return (0, 0);
        uint256 q = best / RETARGET;             // newest period index
        uint256 off = best % RETARGET;           // blocks into period q (of newest)
        uint256 have = best + 1;                 // total blocks mined so far
        uint256 totalBlocks = have < windowBlocks ? have : windowBlocks;
        uint256 remaining = totalBlocks;
        uint256 area;

        // newest period contributes `off+1` blocks (it is still open); the
        // preceding periods contribute at most 2016 each; oldest partial at the
        // far end trims to `remaining`.
        for (uint256 j = 0; j < cap; ++j) {
            if (q < j) break;                    // ring index underflows
            uint256 idx = q - j;
            if (idx >= totalPeriods) break;      // period not yet pushed
            uint256 d = ring[idx % cap];
            if (d == 0) break;
            uint256 take = (j == 0) ? off + 1 : RETARGET;
            if (take > remaining) take = remaining;
            area += d * take;
            remaining -= take;
            if (remaining == 0) break;
        }
        return (area, totalBlocks);
    }

    /// Smoothed difficulty ratio (D_s/D_s0)^b as 64.64, continuous in blocks.
    function difficultyRatio() public view returns (int256) {
        if (anchorWeight == 0) revert NotAnchored();
        uint256 best = bridge.getBtcBlockchainBestChainHeight();
        (uint256 area, uint256 blocks_) = _scanBlocks(best);
        if (area == 0 || blocks_ == 0) revert NotAnchored();
        int256 curLog = FixedPointMath.log2(area) - FixedPointMath.log2(blocks_);
        return FixedPointMath.exp2(FixedPointMath.mul(exponent, anchorWeight - curLog));
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
    /// Freeze D_s0 from the current window (anyone may anchor at mint).
    function anchor() external {
        refresh();
        uint256 best = bridge.getBtcBlockchainBestChainHeight();
        (uint256 area, uint256 blocks_) = _scanBlocks(best);
        if (area == 0 || blocks_ == 0) revert NotAnchored();
        anchorWeight = FixedPointMath.log2(area) - FixedPointMath.log2(blocks_);
        emit Anchored(best);
    }

    /// Re-read the bridge only if best height has advanced into a new epoch.
    /// Backfills every period crossed since the last refresh (so a long gap
    /// still rebuilds the full window), one bridge read per new epoch.
    function refresh() public {
        uint256 best = bridge.getBtcBlockchainBestChainHeight();
        uint256 epoch = best / RETARGET;
        if (totalPeriods != 0 && epoch < totalPeriods) return; // no new epoch

        uint256 first = totalPeriods == 0 ? 0 : totalPeriods;
        for (uint256 e = first; e <= epoch; e++) {
            uint256 h = e * RETARGET;             // first block of period e
            uint256 t = readNBitsAtHeight(h);
            if (t == 0) revert ZeroNBits();
            uint256 d = D_SCALE / t;
            if (d == 0) revert ZeroNBits();
            ring[e % cap] = d;
            currentTarget = t;
        }

        totalPeriods = epoch + 1;
        emit Retargeted(currentTarget, epoch, best);
    }
}