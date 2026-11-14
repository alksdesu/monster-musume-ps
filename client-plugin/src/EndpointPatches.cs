using BepInEx.Logging;
using HarmonyLib;
using NtdServerApiInterface.Core.Application;
using NtdServerApiInterface.Core.Domain.System;
using DMM.Games.Client.Sdk.Connection;

namespace MonmusuTd.Local;

internal static class EndpointPatches
{
    private static EndpointAddress address = null!;
    private static ManualLogSource log = null!;
    private static int redirects;
    private static int platformRedirects;

    internal static void Install(Harmony harmony, EndpointAddress endpointAddress, ManualLogSource logger)
    {
        address = endpointAddress;
        log = logger;
        redirects = 0;
        platformRedirects = 0;
        var target = AccessTools.Method(typeof(NtdServerRequestService), "MakeRequestUrl", new[] { typeof(IRequestNdt) })
            ?? throw new MissingMethodException(typeof(NtdServerRequestService).FullName, "MakeRequestUrl");
        harmony.Patch(target, postfix: new HarmonyMethod(typeof(EndpointPatches), nameof(AfterMakeRequestUrl)));
        var send = AccessTools.Method(typeof(NetgameApiConnector), "Send",
            new[] { typeof(Il2CppSystem.Action<string>), typeof(Il2CppSystem.Action<string>) })
            ?? throw new MissingMethodException(typeof(NetgameApiConnector).FullName, "Send");
        harmony.Patch(send, prefix: new HarmonyMethod(typeof(EndpointPatches), nameof(BeforePlatformSend)));
    }

    private static void AfterMakeRequestUrl(ref string __result)
    {
        __result = address.Redirect(__result);
        if (Interlocked.Increment(ref redirects) == 1)
            log.LogInfo($"Native API redirect active: {new Uri(__result).AbsolutePath}");
    }

    private static void BeforePlatformSend(NetgameApiConnector __instance)
    {
        __instance.url = address.Redirect(__instance.url);
        if (Interlocked.Increment(ref platformRedirects) == 1)
            log.LogInfo($"Native platform redirect active: {new Uri(__instance.url).AbsolutePath}");
    }
}
