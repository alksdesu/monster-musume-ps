using BepInEx.Logging;
using HarmonyLib;
using UnityEngine;

namespace MonmusuTd.Local;

// Turns the pixelation the game lays over its Spine scenes at run time; what is drawn into the art itself stays.
internal static class MosaicDial
{
    private const string Scale = "_Scale";
    private const string Tint = "_Color";
    private const string Mask = "MaskMosaic";
    private const float Shipped = 1f;

    private static float scale = Shipped;
    private static ManualLogSource log = null!;

    internal static void Install(Harmony harmony, float wanted, ManualLogSource logger)
    {
        scale = wanted;
        log = logger;
        if (scale == Shipped)
            return;
        var start = AccessTools.Method(typeof(SpineMosaic), "Start");
        if (start is null)
        {
            log.LogWarning("Mosaic dial cannot reach SpineMosaic.Start; the mosaic stays as shipped.");
            return;
        }
        harmony.Patch(start, prefix: new HarmonyMethod(typeof(MosaicDial), nameof(BeforeStart)),
            postfix: new HarmonyMethod(typeof(MosaicDial), nameof(AfterStart)));
        log.LogInfo(scale > 0 ? $"Mosaic dial: blocks at {scale:0.###} of the shipped size." : "Mosaic dial: the run-time layer is off.");
    }

    // The game copies this material onto every masked slot, so the size is set before it does.
    private static void BeforeStart(SpineMosaic __instance)
    {
        if (scale <= 0)
            return;
        Guarded(() =>
        {
            var material = __instance.MosaicMaterial;
            if (material is not null)
                material.SetFloat(Scale, scale);
        });
    }

    // A material with no colour draws nothing, which leaves the art under the mask as it is.
    private static void AfterStart(SpineMosaic __instance)
    {
        if (scale > 0)
            return;
        Guarded(() =>
        {
            var renderer = __instance.GetComponent<MeshRenderer>();
            var source = renderer?.sharedMaterial;
            if (source is null)
                return;
            var clear = new Material(source);
            clear.SetColor(Tint, Color.clear);
            foreach (var slot in __instance.GetSlotList())
                if (slot.Contains(Mask))
                    __instance.SetCustomSlotMaterial(slot, clear);
        });
    }

    private static void Guarded(Action action)
    {
        // An exception escaping into native code would take the game down with it.
        try
        {
            action();
        }
        catch (Exception error)
        {
            log.LogError($"Mosaic dial failed: {error}");
        }
    }
}
