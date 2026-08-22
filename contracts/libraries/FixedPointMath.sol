// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Signed 64.64 fixed-point arithmetic (value * 2**64) used to compute
///         the fractional-power price law  price = (D/D0)^b  on-chain.
///
///         Solidity has no floating point and the exponent `b` (~0.69) is not an
///         integer, so we evaluate  x^b  as  2^( b * log2(x) ):
///           - ln(x)   via a bit-normalised atanh series (exact to ~Q64),
///           - log2    = ln(x) / ln(2),
///           - exp(2^z) splits the integer part (a pure bit shift) and evaluates
///             the fractional part with a fast-converging Taylor series.
///         All intermediates use int256 (with guarded shifts) so Rootstock/RSK
///         Bitcoin difficulty targets (~up to 2^224) never overflow.
library FixedPointMath {
    /// 2**64, the fixed-point scale: a number `v.REAL * 2**64`.
    int256 internal constant SCALE = 0x10000000000000000; // 2^64
    /// ln(2) * 2^64  (~0.69314718 * 2^64).
    int256 internal constant LN2_64 = 0x0B17217F7D1CF79A;
    /// 1 / ln(2) * 2^64  (~1.44269504 * 2^64).
    int256 internal constant INV_LN2_64 = 0x171547652B82FE000;

    /// Highest set bit index of `x`. `x` is a plain unsigned integer.
    function msb(uint256 x) internal pure returns (uint256) {
        uint256 r = 0;
        if (x >= 0x1 << 128) { x >>= 128; r += 128; }
        if (x >= 0x1 << 64) { x >>= 64; r += 64; }
        if (x >= 0x1 << 32) { x >>= 32; r += 32; }
        if (x >= 0x1 << 16) { x >>= 16; r += 16; }
        if (x >= 0x1 << 8) { x >>= 8; r += 8; }
        if (x >= 0x1 << 4) { x >>= 4; r += 4; }
        if (x >= 0x1 << 2) { x >>= 2; r += 2; }
        if (x >= 0x1 << 1) { r += 1; }
        return r;
    }

    /// Multiply two 64.64 numbers -> 64.64.
    function mul(int256 a, int256 b) internal pure returns (int256) {
        return (a * b) >> 64;
    }

    /// Natural log of a raw positive integer `x`, returned as 64.64.
    function ln(uint256 x) internal pure returns (int256) {
        require(x != 0, "ln(0)");
        int256 e = int256(msb(x));           // e = floor(log2(x))
        uint256 f;                           // mantissa x/2^e in [1, 2), Q64
        if (e >= 64) f = x >> (uint256(e - 64));
        else f = x << (uint256(64 - e));

        // y = (f - 1) / (f + 1), Q64. f is within (0, 2] so no overflow.
        int256 y = (int256(f) - SCALE) * SCALE / (int256(f) + SCALE);
        // ln(f) = 2 * (y + y^3/3 + y^5/5 + ...)
        int256 term = y;
        int256 y2 = y * y >> 64;
        int256 sum = y;
        for (uint256 k = 1; k <= 32; k++) {
            term = term * y2 >> 64;
            if (term == 0) break;
            sum += term / int256(2 * k + 1);
        }
        int256 lnf = 2 * sum;
        return e * LN2_64 + lnf;             // e * ln(2) + ln(f)
    }

    /// Base-2 logarithm of a raw positive integer `x`, returned as 64.64.
    function log2(uint256 x) internal pure returns (int256) {
        return mul(ln(x), INV_LN2_64);
    }

    /// 2^z for a 64.64 exponent `z`; result is 64.64. Handles negative z.
    function exp2(int256 z) internal pure returns (int256) {
        int256 n = z >> 64;                  // floor(z), may be negative
        int256 frac = z - (n << 64);         // fractional part in [0, 1)
        // u = frac * ln(2) in [0, ln2], expand e^u via Taylor (fast convergence).
        int256 u = frac * LN2_64 >> 64;
        int256 term = SCALE;
        int256 s = SCALE;
        for (uint256 k = 1; k <= 30; k++) {
            term = term * u >> 64;
            term = term / int256(k);
            s += term;
            if (term == 0) break;
        }
        if (n >= 0) return s << uint256(n);
        else return s >> uint256(-n);
    }

    /// (x)^b for a raw integer base `x` and a 64.64 exponent `b` (>= 0).
    function powRaw(uint256 x, int256 b) internal pure returns (int256) {
        return exp2(mul(b, log2(x)));
    }
}