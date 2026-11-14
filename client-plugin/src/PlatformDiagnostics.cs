using BepInEx.Logging;
using DMM.Games.Client.Sdk;
using HarmonyLib;

namespace MonmusuTd.Local;

internal static class PlatformDiagnostics
{
    private static ManualLogSource log = null!;

    internal static void Install(Harmony harmony, ManualLogSource logger)
    {
        log = logger;
        var target = AccessTools.Method(typeof(DMMClientGameManager), "OnFailureCallback",
            new[] { typeof(NetGameApiSdk.Kind), typeof(NetGameApiSdk), typeof(NetGameApiSdk.ErrorCode) })
            ?? throw new MissingMethodException(typeof(DMMClientGameManager).FullName, "OnFailureCallback");
        harmony.Patch(target, postfix: new HarmonyMethod(typeof(PlatformDiagnostics), nameof(AfterFailure)));
    }

    private static void AfterFailure(NetGameApiSdk.Kind __0, NetGameApiSdk.ErrorCode __2) =>
        log.LogError($"Platform request failed: {__0}, error={__2} ({(int)__2}).");
}
