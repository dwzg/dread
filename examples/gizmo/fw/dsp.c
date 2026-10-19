/* Gizmo's signal path, in the Cortex-M4 build only (GIZMO_M4: hard float on FPv4-SP and the DSP
   extension): a low-pass filter and an average on floats, a reading converted to millivolts with
   rounding, and saturating and packed arithmetic through the ACLE intrinsics. Key 'f' runs one
   step of it on the configured volume (main.c). */
#include <arm_acle.h>
#include "gizmo.h"

static float g_level;               /* the filter's state */

float lp_filter(float state, float sample, float alpha)
{
    return state + alpha * (sample - state);
}

float average(const float *v, int n)
{
    float s = 0.0f;
    int i;
    for (i = 0; i < n; i++)
        s += v[i];
    return n > 0 ? s / (float)n : 0.0f;
}

int to_millivolts(float volts)
{
    float mv = volts * 1000.0f;
    return (int)(mv >= 0.0f ? mv + 0.5f : mv - 0.5f);
}

s32 mix_sat(s32 a, s32 b)
{
    return __qadd(a, b);                /* saturating: no wrap past INT32_MAX */
}

s32 dot_pairs(s32 a, s32 b, s32 acc)
{
    return __smlad(a, b, acc);          /* two 16-bit products added to acc */
}

s32 clamp12(s32 x)
{
    return __ssat(x, 12);               /* to -2048..2047 */
}

u32 level8(s32 x)
{
    return __usat(x, 8);                /* to 0..255 */
}

int signal_step(u32 volume)
{
    float volts = (float)volume * 0.033f;
    g_level = lp_filter(g_level, volts, 0.25f);
    return to_millivolts(g_level);
}
