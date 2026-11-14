using BepInEx;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;

namespace MonmusuTd.Local;

[BepInPlugin(Id, "MonmusuTd Local PC", "0.1.0")]
public sealed class Plugin : BasePlugin
{
    internal const string Id = "local.monmusutd.pc";
    private readonly Harmony harmony = new(Id);

    public override void Load()
    {
        var enabled = Config.Bind("Local", "Enabled", true, "Use the local game server.");
        var baseUrl = Config.Bind("Local", "BaseUrl", "http://127.0.0.1:18621", "Game server origin.");
        var schedule = Config.Bind("Local", "Schedule", true, "Move activity dates to the game server's schedule.");
        var font = Config.Bind("Local", "Font", string.Empty, "Font file for characters the game's own font lacks; empty picks a system font.");
        var family = Config.Bind("Local", "FontFamily", "Microsoft YaHei,SimHei", "Installed font families for story text, tried in order.");
        var mosaic = Config.Bind("Local", "Mosaic", 1f, "Size of the mosaic blocks the game lays over Spine scenes at run time: 1 as shipped, smaller is finer, 0 leaves that layer out. The mosaic drawn into the art itself stays.");
        if (!enabled.Value)
        {
            Log.LogInfo("Local server adapter disabled.");
            return;
        }

        var address = new EndpointAddress(baseUrl.Value);
        EndpointPatches.Install(harmony, address, Log);
        PlatformDiagnostics.Install(harmony, Log);
        var server = new GameServer(address);
        MasterPatch.Install(harmony, server, schedule.Value, Log);
        StoryText.Install(harmony, server, Log);
        FontFallback.Install(harmony, font.Value, family.Value, Log);
        MosaicDial.Install(harmony, mosaic.Value, Log);
        Log.LogInfo($"Local game server: {address.Origin}");
    }

    public override bool Unload()
    {
        harmony.UnpatchSelf();
        return true;
    }
}
