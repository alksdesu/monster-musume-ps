using BepInEx.Logging;
using HarmonyLib;
using Il2CppInterop.Runtime;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using TMPro;
using UnityEngine;
using UnityEngine.TextCore.LowLevel;
using UnityEngine.UI;
using Utage;

namespace MonmusuTd.Local;

// Stands a system font behind the game's baked Japanese one, which lacks half the characters Chinese is written with.
internal static class FontFallback
{
    private delegate void CreateFromPath(IntPtr font, IntPtr path);
    private delegate void CreateFromFamilies(IntPtr font, IntPtr families, int size);

    // The managed constructors behind these were stripped from the game; the engine calls were not.
    private const string CreateFontFromPath = "UnityEngine.Font::Internal_CreateFontFromPath";
    private const string CreateDynamicFont = "UnityEngine.Font::Internal_CreateDynamicFont";
    // Sampled like the game's own font, so its outline and shadow presets fit the borrowed glyphs too.
    private const int SamplingSize = 48;
    private const int Padding = 9;
    private const int AtlasSize = 4096;
    // The game's font lays lines out by these shares of its size; a taller face would push every line apart.
    private const float Ascent = 0.88f;
    private const float Descent = -0.12f;
    private const float LineHeight = 2f;
    private const float Baseline = 1.5f;
    private const int PlainSize = 16;
    private const string GameFonts = "FOT-";

    private static readonly string[] candidates = { "msyhbd.ttc", "msyh.ttc", "simhei.ttf" };
    // The game unloads what nothing refers to when it changes scenes.
    private static readonly Lazy<Font?> file = new(() => Made("the font file", FromFile));
    private static readonly Lazy<Font?> installed = new(() => Made("the installed font", FromFamilies));
    private static ManualLogSource log = null!;
    private static string chosen = string.Empty;
    private static string[] families = Array.Empty<string>();
    private static bool settled;
    private static TMP_FontAsset? fallback;

    internal static void Install(Harmony harmony, string fontFile, string fontFamilies, ManualLogSource logger)
    {
        log = logger;
        chosen = fontFile;
        families = fontFamilies.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        // The text settings exist only once the engine is up, which the first text to wake is proof of.
        Attach(harmony, new[] { typeof(TextMeshProUGUI), typeof(TextMeshPro) }, "Awake", prefix: nameof(BeforeText));
        // Story text is drawn by the engine's older text, which fills a missing character from a thin system font.
        Attach(harmony, new[] { typeof(Text), typeof(UguiNovelText) }, "OnEnable", postfix: nameof(AfterPlainText));
    }

    private static void Attach(Harmony harmony, Type[] texts, string name, string? prefix = null, string? postfix = null)
    {
        foreach (var text in texts)
        {
            var method = AccessTools.DeclaredMethod(text, name, Type.EmptyTypes);
            if (method is null)
                log.LogWarning($"Font fallback cannot reach {text.Name}.{name}.");
            else
                harmony.Patch(method, Hook(prefix), Hook(postfix));
        }
    }

    private static HarmonyMethod? Hook(string? name) => name is null ? null : new HarmonyMethod(typeof(FontFallback), name);

    private static void BeforeText()
    {
        if (settled)
            return;
        // An exception escaping into native code would take the game down with it.
        try
        {
            settled = Stand();
        }
        catch (Exception error)
        {
            settled = true;
            log.LogError($"Font fallback failed: {error}");
        }
    }

    private static void AfterPlainText(Text __instance)
    {
        try
        {
            var shown = __instance.font;
            // Only the game's own Japanese font gives way; a line in one face reads better than two weights mixed.
            if (shown is null || !shown.name.StartsWith(GameFonts, StringComparison.Ordinal) || installed.Value is not { } font)
                return;
            __instance.font = font;
            // Of an installed family only the bold face is as heavy as the font it replaces.
            __instance.fontStyle = FontStyle.Bold;
        }
        catch (Exception error)
        {
            log.LogError($"Font fallback failed on story text: {error}");
        }
    }

    private static Font? Made(string what, Func<Font?> make)
    {
        try
        {
            return make();
        }
        catch (Exception error)
        {
            log.LogError($"Font fallback failed on {what}: {error}");
            return null;
        }
    }

    // The engine's own half of a font exists only once one of its calls has filled the blank in.
    private static Font Filled(string name, Action<IntPtr> fill)
    {
        var font = new Font(IL2CPP.il2cpp_object_new(Il2CppClassPointerStore<Font>.NativeClassPtr));
        fill(font.Pointer);
        font.hideFlags = HideFlags.HideAndDontSave;
        font.name = name;
        return font;
    }

    private static Font? FromFile()
    {
        var fonts = Environment.GetFolderPath(Environment.SpecialFolder.Fonts);
        var path = chosen.Length > 0
            ? (File.Exists(chosen) ? chosen : null)
            : candidates.Select(name => Path.Combine(fonts, name)).FirstOrDefault(File.Exists);
        if (path is null)
        {
            log.LogWarning("Font fallback found no font file; characters the game's font lacks stay blank.");
            return null;
        }
        var create = IL2CPP.ResolveICall<CreateFromPath>(CreateFontFromPath);
        var font = Filled(Path.GetFileNameWithoutExtension(path), blank => create(blank, IL2CPP.ManagedStringToIl2Cpp(path)));
        log.LogInfo($"Font fallback: {path} stands behind the game's fonts.");
        return font;
    }

    // A font read from a file has no texture of its own, which the older text draws from.
    private static Font? FromFamilies()
    {
        if (families.Length == 0)
            return null;
        var create = IL2CPP.ResolveICall<CreateFromFamilies>(CreateDynamicFont);
        var names = new Il2CppStringArray(families);
        var font = Filled(families[0], blank => create(blank, names.Pointer, PlainSize));
        log.LogInfo($"Font fallback: story text is drawn in {string.Join(", ", families)}.");
        return font;
    }

    private static bool Stand()
    {
        var settings = TMP_Settings.instance;
        if (settings is null)
            return false;
        if (file.Value is not { } font)
            return true;
        var asset = TMP_FontAsset.CreateFontAsset(
            font, SamplingSize, Padding, GlyphRenderMode.SDFAA, AtlasSize, AtlasSize, AtlasPopulationMode.Dynamic, true);
        if (asset is null)
        {
            log.LogWarning($"Font fallback could not build a font from {font.name}.");
            return true;
        }
        asset.name = font.name + " SDF";
        asset.hideFlags = HideFlags.HideAndDontSave;
        asset.material.hideFlags = HideFlags.HideAndDontSave;
        foreach (var atlas in asset.atlasTextures)
            atlas.hideFlags = HideFlags.HideAndDontSave;
        var face = asset.faceInfo;
        face.ascentLine = Ascent * face.pointSize;
        face.descentLine = Descent * face.pointSize;
        face.lineHeight = LineHeight * face.pointSize;
        face.baseline = Baseline;
        asset.faceInfo = face;
        var fallbacks = TMP_Settings.fallbackFontAssets;
        if (fallbacks is null)
            settings.m_fallbackFontAssets = fallbacks = new Il2CppSystem.Collections.Generic.List<TMP_FontAsset>();
        fallbacks.Add(asset);
        fallback = asset;
        return true;
    }
}
