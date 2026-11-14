using System.Globalization;
using System.Text.Json;
using BepInEx.Logging;
using HarmonyLib;
using ntd.database;
using UnityEngine;

namespace MonmusuTd.Local;

// Writes the game server's content changes and schedule into the master tables the game has just loaded.
internal static class MasterPatch
{
    private const string Route = "/masters/patch";
    private const string ShippedDates = "?dates=shipped";

    private sealed record Written(string Row, PatchWrite Change, PatchScalar Original, PatchScalar Stored);

    private sealed record Ledger(PatchTable Table, List<Written> Entries);

    private const string Held = "m_Item2";

    private static readonly Dictionary<string, Ledger> ledgers = new();
    private static GameServer server = null!;
    private static ManualLogSource log = null!;
    private static string route = Route;
    private static PatchPlan plan = PatchPlan.Empty;

    internal static void Install(Harmony harmony, GameServer gameServer, bool scheduled, ManualLogSource logger)
    {
        server = gameServer;
        log = logger;
        route = scheduled ? Route : Route + ShippedDates;
        // Every preloaded table is in memory by now and nothing has read a value out of them yet.
        var init = AccessTools.Method(typeof(GameDataFactory), "Init", Type.EmptyTypes)
            ?? throw new MissingMethodException(nameof(GameDataFactory), "Init");
        harmony.Patch(init, prefix: new HarmonyMethod(typeof(MasterPatch), nameof(BeforeDataInit)));
        // The pool tables load later; this is the last moment before the game indexes them.
        var pools = AccessTools.Method(typeof(GachaDataBase), "BuildEnclosureIndex", Type.EmptyTypes);
        if (pools is null)
            log.LogWarning("Master patch cannot reach the tables that load late; they stay as shipped.");
        else
            harmony.Patch(pools, prefix: new HarmonyMethod(typeof(MasterPatch), nameof(BeforePoolIndex)));
    }

    private static void BeforeDataInit() => Guarded(() =>
    {
        plan = Fetch();
        Apply(late: false);
    });

    private static void BeforePoolIndex() => Guarded(() => Apply(late: true));

    private static void Guarded(Action work)
    {
        // An exception escaping into native code would take the game down with it.
        try
        {
            work();
        }
        catch (Exception error)
        {
            log.LogError($"Master patch failed: {error}");
        }
    }

    private static PatchPlan Fetch()
    {
        try
        {
            return PatchPlan.Parse(server.Read(route), reason => log.LogWarning($"Master patch entry skipped: {reason}"));
        }
        catch (Exception error) when (error is HttpRequestException or OperationCanceledException or JsonException
            or KeyNotFoundException or InvalidOperationException or FormatException)
        {
            log.LogWarning($"Master patch unavailable, master data stays as shipped: {error.Message}");
            return PatchPlan.Empty;
        }
    }

    private static MasterMemory.Value Loaded(DataBaseManager manager, PatchTable shape)
    {
        if (shape.Folder is null)
        {
            var cache = manager.m_cache;
            if (!cache.ContainsKey(shape.Asset))
                throw new InvalidOperationException("the table is not loaded");
            return MasterMemory.Heap(cache[shape.Asset].Pointer);
        }
        var folders = manager.m_cacheSet;
        if (!folders.ContainsKey(shape.Folder))
            throw new InvalidOperationException("the folder is not loaded");
        return MasterMemory.Heap(folders[shape.Folder].Pointer);
    }

    private static void Apply(bool late)
    {
        var manager = DataBaseManager.Instance;
        var planned = plan.Tables.Where(table => table.Late == late).ToDictionary(table => table.Asset);
        var kept = ledgers.Where(pair => pair.Value.Table.Late == late).Select(pair => pair.Key);
        var written = 0;
        using var shared = MasterMemory.Share();
        foreach (var asset in planned.Keys.Union(kept).ToList())
        {
            try
            {
                var shape = planned.GetValueOrDefault(asset) ?? ledgers[asset].Table;
                var table = Loaded(manager, shape);
                // The game keeps its tables for the life of the process, so last round's values are still in them.
                if (ledgers.Remove(asset, out var previous))
                    Restore(table, previous, shared);
                if (planned.TryGetValue(asset, out var changes))
                    written += Write(table, changes, shared);
            }
            catch (Exception error)
            {
                log.LogError($"Master patch skipped {asset}: {error.Message}");
            }
        }
        var revision = plan.Revision.Length == 0 ? "empty" : plan.Revision;
        log.LogInfo($"Master patch {revision}: {written} values written in {planned.Count} {(late ? "late" : "preloaded")} tables.");
    }

    private static Dictionary<string, MasterMemory.Value> Rows(MasterMemory.Value table, PatchTable shape)
    {
        var found = new Dictionary<string, MasterMemory.Value>();
        var position = 0;
        foreach (var row in shape.Folder is null ? Flattened(table, shape.Rows, 0) : Listed(table))
        {
            var identity = shape.ByPosition
                ? position.ToString(CultureInfo.InvariantCulture)
                : PatchPlan.Identity(shape.Key.Select(field => KeyText(MasterMemory.Read(MasterMemory.Field(row, field)))));
            found[identity] = row;
            position++;
        }
        if (position == shape.Count)
            return found;
        var difference = $"the game holds {position} rows where the server holds {shape.Count}";
        // A place in the table names the same row only while both sides hold the same table.
        if (shape.ByPosition)
            throw new InvalidOperationException(difference);
        log.LogWarning($"Master patch {shape.Asset}: {difference}; rows are matched by key.");
        return found;
    }

    // A folder's objects are listed in pairs of their number and the object itself.
    private static IEnumerable<MasterMemory.Value> Listed(MasterMemory.Value list)
    {
        var pairs = MasterMemory.Items(list);
        for (var index = 0; index < pairs.Count; index++)
            yield return MasterMemory.Open(MasterMemory.Field(MasterMemory.Open(pairs[index]), Held));
    }

    private static IEnumerable<MasterMemory.Value> Flattened(MasterMemory.Value holder, IReadOnlyList<string> fields, int depth)
    {
        var items = MasterMemory.Items(MasterMemory.Open(MasterMemory.Field(holder, fields[depth])));
        for (var index = 0; index < items.Count; index++)
        {
            var item = MasterMemory.Open(items[index]);
            if (depth + 1 == fields.Count)
                yield return item;
            else
                foreach (var inner in Flattened(item, fields, depth + 1))
                    yield return inner;
        }
    }

    private static string KeyText(PatchScalar value) => value.Kind switch
    {
        PatchKind.Whole => value.Whole.ToString(CultureInfo.InvariantCulture),
        PatchKind.Text => value.Text ?? string.Empty,
        _ => throw new InvalidOperationException("a row cannot be named by a fraction"),
    };

    private static (MasterMemory.Slot Slot, List<MasterMemory.Slot> Caches) Locate(MasterMemory.Value row, PatchWrite write)
    {
        var holder = row;
        // What the game works out from a value is kept on the object that owns it.
        var host = row;
        MasterMemory.Slot slot = default;
        for (var position = 0; position < write.Path.Count; position++)
        {
            var step = write.Path[position];
            slot = step.Field is null ? MasterMemory.Items(holder)[step.Index] : MasterMemory.Field(holder, step.Field);
            if (position + 1 == write.Path.Count)
                break;
            holder = MasterMemory.Open(slot);
            if (MasterMemory.Owns(holder))
                host = holder;
        }
        return (slot, write.Forget.Select(name => MasterMemory.Cache(host, name)).ToList());
    }

    private static int Write(MasterMemory.Value table, PatchTable changes, MasterMemory.Shared shared)
    {
        var rows = Rows(table, changes);
        var ledger = new Ledger(changes, new List<Written>());
        ledgers[changes.Asset] = ledger;
        foreach (var change in changes.Changes)
        {
            if (!rows.TryGetValue(change.Identity, out var row))
            {
                log.LogWarning($"Master patch row missing in {changes.Asset}: {PatchPlan.Describe(change.Identity)}");
                continue;
            }
            foreach (var write in change.Writes)
            {
                Attempted(changes.Asset, change.Identity, write, () =>
                {
                    var (slot, caches) = Locate(row, write);
                    var original = MasterMemory.Read(slot);
                    MasterMemory.Write(slot, write.Value, shared);
                    // What is read back is what a later round has to recognise, rounding included.
                    ledger.Entries.Add(new Written(change.Identity, write, original, MasterMemory.Read(slot)));
                    caches.ForEach(MasterMemory.Forget);
                });
            }
        }
        Forget(table, changes);
        return ledger.Entries.Count;
    }

    private static void Restore(MasterMemory.Value table, Ledger ledger, MasterMemory.Shared shared)
    {
        var rows = Rows(table, ledger.Table);
        foreach (var entry in ledger.Entries)
        {
            if (!rows.TryGetValue(entry.Row, out var row))
                continue;
            Attempted(ledger.Table.Asset, entry.Row, entry.Change, () =>
            {
                var (slot, caches) = Locate(row, entry.Change);
                // A table read from disk again already carries its shipped values.
                if (MasterMemory.Read(slot) != entry.Stored)
                    return;
                MasterMemory.Write(slot, entry.Original, shared);
                caches.ForEach(MasterMemory.Forget);
            });
        }
        Forget(table, ledger.Table);
    }

    private static void Forget(MasterMemory.Value table, PatchTable shape)
    {
        foreach (var name in shape.Forget)
        {
            try
            {
                MasterMemory.Forget(MasterMemory.Cache(table, name));
            }
            catch (Exception error) when (error is InvalidOperationException or MissingFieldException)
            {
                log.LogWarning($"Master patch could not clear {name} of {shape.Asset}: {error.Message}");
            }
        }
    }

    private static void Attempted(string asset, string row, PatchWrite write, Action work)
    {
        try
        {
            work();
        }
        catch (Exception error) when (error is InvalidOperationException or MissingFieldException)
        {
            log.LogWarning($"Master patch value skipped in {asset} {PatchPlan.Describe(row)} {write.Where}: {error.Message}");
        }
    }
}
