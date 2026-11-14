using System.Globalization;
using System.Text.Json;

namespace MonmusuTd.Local;

internal enum PatchKind
{
    Whole,
    Real,
    Text,
}

internal readonly record struct PatchScalar(PatchKind Kind, long Whole, double Real, string? Text)
{
    internal static PatchScalar Of(long whole) => new(PatchKind.Whole, whole, whole, null);

    internal static PatchScalar Of(double real) => new(PatchKind.Real, 0, real, null);

    internal static PatchScalar Of(string? text) => new(PatchKind.Text, 0, 0, text);

    public override string ToString() => Kind switch
    {
        PatchKind.Whole => Whole.ToString(CultureInfo.InvariantCulture),
        PatchKind.Real => Real.ToString("R", CultureInfo.InvariantCulture),
        _ => Text is null ? "nothing" : $"\"{Text}\"",
    };
}

internal readonly record struct PatchStep(string? Field, int Index)
{
    public override string ToString() => Field ?? $"[{Index.ToString(CultureInfo.InvariantCulture)}]";
}

internal sealed record PatchWrite(IReadOnlyList<PatchStep> Path, PatchScalar Value, IReadOnlyList<string> Forget)
{
    internal string Where => string.Concat(
        Path.Select((step, position) => position > 0 && step.Field is not null ? $".{step}" : step.ToString()));
}

internal sealed record PatchRow(string Identity, IReadOnlyList<PatchWrite> Writes);

// A table is one asset holding rows; a folder is the game's list of objects read in one by one.
internal sealed record PatchTable(
    string Asset,
    string? Folder,
    IReadOnlyList<string> Rows,
    IReadOnlyList<string> Key,
    int Count,
    bool Late,
    IReadOnlyList<string> Forget,
    IReadOnlyList<PatchRow> Changes)
{
    internal bool ByPosition => Key.Count == 0;
}

internal sealed record PatchPlan(string Revision, IReadOnlyList<PatchTable> Tables)
{
    internal const char KeySeparator = '\u001f';

    internal static PatchPlan Empty { get; } = new(string.Empty, Array.Empty<PatchTable>());

    internal static string Identity(IEnumerable<string> parts) => string.Join(KeySeparator, parts);

    internal static string Describe(string identity) => identity.Replace(KeySeparator, ' ');

    internal static PatchPlan Parse(string json, Action<string> reject)
    {
        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;
        var tables = new List<PatchTable>();
        foreach (var table in root.GetProperty("tables").EnumerateArray())
        {
            var folder = table.TryGetProperty("folder", out var named) ? Text(named) : null;
            var asset = folder ?? Text(table.GetProperty("asset"));
            var rows = folder is null ? Texts(table.GetProperty("rows")) : new List<string>();
            var key = Texts(table.GetProperty("key"));
            if (folder is null && rows.Count == 0)
            {
                reject($"{asset}: it does not say where its rows are");
                continue;
            }
            var changes = new List<PatchRow>();
            foreach (var change in table.GetProperty("changes").EnumerateArray())
            {
                var at = change.GetProperty("at").EnumerateArray().Select(KeyText).ToList();
                var identity = Identity(at);
                // A row without key fields is found by its place in the table.
                if (at.Count != Math.Max(key.Count, 1))
                {
                    reject($"{asset} {Describe(identity)}: its key does not fit the table's");
                    continue;
                }
                var writes = new List<PatchWrite>();
                foreach (var write in change.GetProperty("set").EnumerateArray())
                {
                    try
                    {
                        writes.Add(new PatchWrite(
                            write.GetProperty("path").EnumerateArray().Select(Step).ToList(),
                            Scalar(write.GetProperty("value")),
                            Texts(write.GetProperty("forget"))));
                    }
                    catch (Exception error) when (error is JsonException or FormatException or OverflowException)
                    {
                        reject($"{asset} {Describe(identity)}: {error.Message}");
                    }
                }
                if (writes.Count > 0)
                    changes.Add(new PatchRow(identity, writes));
            }
            if (changes.Count > 0)
                tables.Add(new PatchTable(
                    asset,
                    folder,
                    rows,
                    key,
                    table.GetProperty("count").GetInt32(),
                    table.GetProperty("late").GetBoolean(),
                    Texts(table.GetProperty("forget")),
                    changes));
        }
        return new PatchPlan(Text(root.GetProperty("revision")), tables);
    }

    private static PatchStep Step(JsonElement step) => step.ValueKind switch
    {
        JsonValueKind.String => new PatchStep(Text(step), 0),
        JsonValueKind.Number when step.GetInt32() >= 0 => new PatchStep(null, step.GetInt32()),
        _ => throw new JsonException("A patch path is made of field names and positions."),
    };

    private static PatchScalar Scalar(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => PatchScalar.Of(Text(value)),
        JsonValueKind.Number => value.TryGetInt64(out var whole) ? PatchScalar.Of(whole) : PatchScalar.Of(value.GetDouble()),
        _ => throw new JsonException("A patch value is neither a number nor text."),
    };

    private static string KeyText(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => Text(value),
        JsonValueKind.Number => value.GetInt64().ToString(CultureInfo.InvariantCulture),
        _ => throw new JsonException("A patch key is neither a number nor text."),
    };

    private static List<string> Texts(JsonElement values) => values.EnumerateArray().Select(Text).ToList();

    private static string Text(JsonElement value) =>
        value.GetString() ?? throw new JsonException("A patch value is not text.");
}
