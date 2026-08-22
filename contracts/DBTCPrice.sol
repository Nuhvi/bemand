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
/// turned into a price with the fitted power law:
///
///     DBTC per BTC = (D / D0)^b          (BTC "strength", the demand proxy)
///     BTC per DBTC = 1 / (D / D0)^b
///
/// where `D` is Bitcoin's current network difficulty, `D0` is an anchor
/// difficulty fixed at mint time, and `b` (~0.69) is the OLS-fitted exponent
/// (see README/backtest). Since target = MaxTarget/Difficulty, the ratio
/// `D/D0 = target0/target` cancels MaxTarget, so the contract works directly
/// on the compact 32-bit `nBits` targets and only needs one header read.
///
/// Because difficulty only changes once every 2016 blocks, the contract caches
/// a single value per difficulty period: `refresh()` re-reads the bridge only
/// when the best chain height has stepped into a new epoch.
///
/// All price results are returned as signed 64.64 fixed point (value * 2**64).
contract DBTCPrice {
    uint256 internal constant RETARGET = 2016; // blocks per difficulty period

    /// Configurable; set to the RSK Bridge address in deployment.
    IBridge public immutable bridge;

    // ---- anchor (D0 / t0), frozen once at mint ----
    uint256 public anchorTarget;  // compact target of D0 (== MaxTarget / D0)
    uint256 public anchorHeight;  // Bitcoin height the anchor was read from

    // ---- cached current period difficulty (D / t) ----
    uint256 public currentTarget;   // MaxTarget / D   (target of this epoch)
    uint256 public currentHeight;   // first block height of the cached epoch
    uint256 public currentEpoch;    // currentHeight / RETARGET
    uint256 public reads;           // bridge reads so far (cache-hit metric)

    /// OLS exponent `b` (≈ 0.69), fixed at deployment in 64.64 form.
    int256 public immutable exponent;

    event Anchored(uint256 target, uint256 height);
    event Retargeted(uint256 target, uint256 height, uint256 epoch);

    error ZeroNBits();

    /// @param bridge_      RSK Bridge address.
    /// @param exponent_    fitted `b` as 64.64 (e.g. 0.69 * 2**64).
    constructor(address bridge_, int256 exponent_) {
        bridge = IBridge(bridge_);
        exponent = exponent_;
    }

    // ------------------------------------------------------------------ read
    /// Read the Bitcoin target (compact form) at a given chain height from the bridge.
    function readTargetAtHeight(uint256 h)
        public
        view
        returns (uint256 compactNBits)
    {
        bytes memory header = bridge.getBtcBlockchainBlockHeaderByHeight(h);
        // nBits occupies header bytes 72..75, serialised little-endian as the uint32.
        compactNBits = uint256(uint8(header[72]))
            | (uint256(uint8(header[73])) << 8)
            | (uint256(uint8(header[74])) << 16)
            | (uint256(uint8(header[75])) << 24);
    }

    /// Difficulty ratio D/D0 as 64.64, using the target form D/D0 == T0/T.
    /// Public for inspection; called by price getters.
    function difficultyRatio()
        public
        view
        returns (int256)
    {
        uint256 t0 = anchorTarget;
        uint256 t = currentTarget;
        if (t0 == 0 || t == 0) revert ZeroNBits();
        // (D/D0)^b = (T0/T)^b = 2^( b * (log2(T0) - log2(T)) ).
        // Computed via log-space directly so T0*2^64 never overflows.
        int256 L = FixedPointMath.log2(t0) - FixedPointMath.log2(t);
        return FixedPointMath.exp2(FixedPointMath.mul(exponent, L));
    }

    /// DBTC per BTC = (D/D0)^b, 64.64.
    function dbtcPerBtc() external view returns (int256) {
        return difficultyRatio();
    }

    /// BTC per DBTC = 1 / (D/D0)^b, 64.64.
    function btcPerDbtc() external view returns (int256) {
        int256 r = difficultyRatio();
        require(r != 0, "zero ratio");
        return (FixedPointMath.SCALE * FixedPointMath.SCALE) / r;
    }

    // -------------------------------------------------------------- mutating
    /// Set the anchor difficulty from the current best-period difficulty.
    function anchor() external {
        refresh();
        anchorTarget = currentTarget;
        anchorHeight = currentHeight;
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
        reads++;
        emit Retargeted(t, h, epoch);
    }
}