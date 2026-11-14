using System.Text.Json;
using BepInEx.Logging;
using HarmonyLib;
using Utage;

namespace MonmusuTd.Local;

// Shows the game server's translation where the story engine reads a line or names who speaks it.
internal static class StoryText
{
    private const string NamesRoute = "/localization/names";
    private const string ScenesRoute = "/localization/scenes/";
    private const string Names = "names";
    private const string Cards = "cards";
    private const string Lines = "lines";

    private sealed record Owner(int Card, string Name);

    private sealed record Spoken(Dictionary<string, string> Names, Dictionary<string, List<Owner>> Cards);

    private static readonly Dictionary<string, Dictionary<string, string>> scenes = new();
    private static Spoken? spoken;
    private static GameServer server = null!;
    private static ManualLogSource log = null!;

    internal static void Install(Harmony harmony, GameServer gameServer, ManualLogSource logger)
    {
        server = gameServer;
        log = logger;
        // Lines, choices and backlog entries are all read through this one method as they are shown.
        Attach(harmony, typeof(AdvParser), nameof(AdvParser.ParseCellLocalizedText),
            new[] { typeof(StringGridRow), typeof(string) }, nameof(AfterLine));
        // A name is compared with the character's own to open her profile, so only what is shown is changed.
        Attach(harmony, typeof(AdvUguiMessageWindow), nameof(AdvUguiMessageWindow.OnTextChanged),
            new[] { typeof(AdvMessageWindow) }, nameof(AfterWindowText));
        Attach(harmony, typeof(AdvBacklog), "get_" + nameof(AdvBacklog.MainCharacterNameText),
            Type.EmptyTypes, nameof(AfterBacklogName));
        // The card the profile opens is found by name, and the card now carries the translated one.
        Attach(harmony, typeof(AdvCharacterInfo), nameof(AdvCharacterInfo.GetCharacterCardId),
            Type.EmptyTypes, nameof(AfterCardId));
    }

    private static void Attach(Harmony harmony, Type type, string name, Type[] parameters, string postfix)
    {
        var method = AccessTools.Method(type, name, parameters);
        if (method is null)
            log.LogWarning($"Story text cannot reach {type.Name}.{name}; it stays in Japanese there.");
        else
            harmony.Patch(method, postfix: new HarmonyMethod(typeof(StoryText), postfix));
    }

    private static void AfterLine(StringGridRow __0, ref string __result)
    {
        if (string.IsNullOrEmpty(__result))
            return;
        var shown = __result;
        __result = Guarded(shown, () => Scene(__0.Grid.SheetName).GetValueOrDefault(shown, shown));
    }

    private static void AfterWindowText(AdvUguiMessageWindow __instance) => Guarded(string.Empty, () =>
    {
        var box = __instance.nameText;
        if (box is null)
            return string.Empty;
        var shown = box.text;
        var name = Named(shown);
        if (name != shown)
            box.text = name;
        return name;
    });

    private static void AfterBacklogName(ref string __result)
    {
        var shown = __result;
        __result = Guarded(shown, () => Named(shown));
    }

    private static void AfterCardId(AdvCharacterInfo __instance, ref int __result)
    {
        if (__result != 0)
            return;
        // An exception escaping into native code would take the game down with it.
        try
        {
            // The script names a character by the number of her card's art, which her card carries as its resource.
            var number = Digits(__instance.NameText);
            if (number.Length == 0 || !Served().Cards.TryGetValue(number, out var owners))
                return;
            var said = __instance.LocalizeNameText;
            __result = owners.FirstOrDefault(owner => owner.Name == said)?.Card ?? 0;
        }
        catch (Exception error)
        {
            log.LogError($"Story profile lookup failed: {error}");
        }
    }

    private static string Digits(string? text) =>
        text is null ? string.Empty : new string(text.Where(letter => letter is >= '0' and <= '9').ToArray());

    private static string Guarded(string shown, Func<string> translated)
    {
        // An exception escaping into native code would take the game down with it.
        try
        {
            return translated();
        }
        catch (Exception error)
        {
            log.LogError($"Story text failed: {error}");
            return shown;
        }
    }

    private static string Named(string? shown) =>
        string.IsNullOrEmpty(shown) ? shown ?? string.Empty : Served().Names.GetValueOrDefault(shown, shown);

    private static Spoken Served()
    {
        if (spoken is null)
        {
            spoken = Fetch(NamesRoute, root => new Spoken(
                Texts(root.GetProperty(Names)),
                root.GetProperty(Cards).EnumerateObject().ToDictionary(
                    pair => pair.Name,
                    pair => pair.Value.EnumerateArray()
                        .Select(owner => new Owner(owner[0].GetInt32(), owner[1].GetString() ?? string.Empty))
                        .ToList())),
                new Spoken(new Dictionary<string, string>(), new Dictionary<string, List<Owner>>()));
            log.LogInfo($"Story text: {spoken.Names.Count} speakers named, {spoken.Cards.Count} cards known.");
        }
        return spoken;
    }

    private static Dictionary<string, string> Scene(string sheet)
    {
        if (!scenes.TryGetValue(sheet, out var lines))
        {
            scenes[sheet] = lines = Fetch(ScenesRoute + Uri.EscapeDataString(sheet),
                root => Texts(root.GetProperty(Lines)), new Dictionary<string, string>());
            log.LogInfo($"Story text {sheet}: {lines.Count} lines translated.");
        }
        return lines;
    }

    private static Dictionary<string, string> Texts(JsonElement pairs) =>
        pairs.EnumerateObject().ToDictionary(pair => pair.Name, pair => pair.Value.GetString() ?? pair.Name);

    private static Fetched Fetch<Fetched>(string route, Func<JsonElement, Fetched> read, Fetched fallback)
    {
        try
        {
            using var document = JsonDocument.Parse(server.Read(route));
            return read(document.RootElement);
        }
        catch (Exception error) when (error is HttpRequestException or OperationCanceledException or JsonException
            or KeyNotFoundException or InvalidOperationException or IndexOutOfRangeException)
        {
            // Asking again at every line would stall the scene, so it stays in Japanese until the game restarts.
            log.LogWarning($"Story text unavailable from {route}: {error.Message}");
            return fallback;
        }
    }
}
