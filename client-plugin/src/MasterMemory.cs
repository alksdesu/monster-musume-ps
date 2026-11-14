using System.Runtime.InteropServices;
using Il2CppInterop.Runtime;

namespace MonmusuTd.Local;

// Reaches the game's own objects by address; wrapping each of ten thousand rows takes seconds.
internal static class MasterMemory
{
    internal readonly record struct Value(IntPtr Data, IntPtr Class, IntPtr Owner, bool Inline);

    internal readonly record struct Slot(IntPtr Address, IntPtr Owner, int Type, IntPtr Class);

    internal readonly record struct Sequence(IntPtr Data, int Count, int Stride, int Type, IntPtr Class)
    {
        internal Slot this[int index] => index < Count
            ? new Slot(Data + ArrayData + index * Stride, Data, Type, Class)
            : throw new InvalidOperationException($"the game holds {Count} entries here, not {index + 1}");
    }

    // One string a round for each text, held so the game cannot collect it before every slot refers to it.
    internal sealed class Shared : IDisposable
    {
        private readonly Dictionary<string, IntPtr> strings = new();
        private readonly List<IntPtr> handles = new();

        internal IntPtr String(string? text)
        {
            if (text is null)
                return IntPtr.Zero;
            if (!strings.TryGetValue(text, out var pointer))
            {
                pointer = IL2CPP.ManagedStringToIl2Cpp(text);
                handles.Add(IL2CPP.il2cpp_gchandle_new(pointer, false));
                strings[text] = pointer;
            }
            return pointer;
        }

        public void Dispose()
        {
            handles.ForEach(handle => IL2CPP.il2cpp_gchandle_free(handle));
            handles.Clear();
            strings.Clear();
        }
    }

    private readonly record struct Member(int Offset, int Type, IntPtr Class);

    private readonly record struct Range(int Size, long Low, long High);

    private enum Shape
    {
        Plain,
        Vector,
        List,
    }

    private const int ObjectHeader = 0x10;
    private const int ArrayData = 0x20;
    private const int StaticFlag = 0x10;
    private const int Single = 0x0C;
    private const int Double = 0x0D;
    private const int Text = 0x0E;
    private const int Struct = 0x11;
    private const int Class = 0x12;
    private const int Generic = 0x15;
    private const int Object = 0x1C;
    private const int Vector = 0x1D;
    private const string Collections = "System.Collections.Generic";
    private const string ListClass = "List`1";
    private const string ListItems = "_items";
    private const string ListSize = "_size";
    private const string NullableClass = "Nullable`1";

    private static readonly Dictionary<int, Range> wholes = new()
    {
        [0x02] = new(1, 0, 1),
        [0x04] = new(1, sbyte.MinValue, sbyte.MaxValue),
        [0x05] = new(1, byte.MinValue, byte.MaxValue),
        [0x06] = new(2, short.MinValue, short.MaxValue),
        [0x07] = new(2, ushort.MinValue, ushort.MaxValue),
        [0x08] = new(4, int.MinValue, int.MaxValue),
        [0x09] = new(4, uint.MinValue, uint.MaxValue),
        [0x0A] = new(8, long.MinValue, long.MaxValue),
    };
    private static readonly Dictionary<(IntPtr Class, string Field), Member> members = new();
    private static readonly Dictionary<IntPtr, Member> elements = new();
    private static readonly Dictionary<IntPtr, Shape> shapes = new();

    internal static Value Heap(IntPtr pointer) =>
        new(pointer, IL2CPP.il2cpp_object_get_class(pointer), pointer, false);

    internal static bool Owns(Value value) => !value.Inline && ShapeOf(value.Class) == Shape.Plain;

    internal static Slot Field(Value holder, string name)
    {
        var member = Describe(holder.Class, name);
        // Offsets count from an object's header, which a value kept inline does not have.
        var offset = holder.Inline ? member.Offset - ObjectHeader : member.Offset;
        if (offset < 0)
            throw new InvalidOperationException($"{name} is not where a field can be");
        return new Slot(holder.Data + offset, holder.Owner, member.Type, member.Class);
    }

    internal static Value Open(Slot slot)
    {
        if (Inlined(slot))
            return new Value(slot.Address, slot.Class, slot.Owner, true);
        if (slot.Type is not (Class or Generic or Object or Vector))
            throw new InvalidOperationException("the game keeps a single value here");
        var pointer = Marshal.ReadIntPtr(slot.Address);
        return pointer == IntPtr.Zero
            ? throw new InvalidOperationException("the game holds nothing here")
            : Heap(pointer);
    }

    internal static Sequence Items(Value holder)
    {
        var shape = holder.Inline ? Shape.Plain : ShapeOf(holder.Class);
        if (shape == Shape.Vector)
            return Spread(holder);
        if (shape != Shape.List)
            throw new InvalidOperationException("the game keeps no list here");
        // A list keeps spare room in its array; only the first entries are its own.
        var size = Marshal.ReadInt32(Field(holder, ListSize).Address);
        return Spread(Open(Field(holder, ListItems))) with { Count = size };
    }

    internal static PatchScalar Read(Slot slot) => slot.Type switch
    {
        Text => PatchScalar.Of(IL2CPP.Il2CppStringToManaged(Marshal.ReadIntPtr(slot.Address))),
        Single => PatchScalar.Of((double)BitConverter.Int32BitsToSingle(Marshal.ReadInt32(slot.Address))),
        Double => PatchScalar.Of(BitConverter.Int64BitsToDouble(Marshal.ReadInt64(slot.Address))),
        _ => PatchScalar.Of(Whole(slot.Address, Numbers(slot))),
    };

    internal static Shared Share() => new();

    internal static void Write(Slot slot, PatchScalar value, Shared? shared = null)
    {
        switch (slot.Type)
        {
            case Text when value.Kind == PatchKind.Text:
                var text = shared is null ? IL2CPP.ManagedStringToIl2Cpp(value.Text) : shared.String(value.Text);
                IL2CPP.il2cpp_gc_wbarrier_set_field(slot.Owner, slot.Address, text);
                break;
            case Single when value.Kind != PatchKind.Text:
                Marshal.WriteInt32(slot.Address, BitConverter.SingleToInt32Bits((float)value.Real));
                break;
            case Double when value.Kind != PatchKind.Text:
                Marshal.WriteInt64(slot.Address, BitConverter.DoubleToInt64Bits(value.Real));
                break;
            case Text or Single or Double:
                throw Misfit(value);
            default:
                var range = Numbers(slot);
                if (value.Kind != PatchKind.Whole || value.Whole < range.Low || value.Whole > range.High)
                    throw Misfit(value);
                Store(slot.Address, range.Size, value.Whole);
                break;
        }
    }

    internal static Slot Cache(Value host, string name)
    {
        var slot = Field(host, name);
        var kept = slot.Type is Class or Text or Object or Vector
            || (slot.Type == Generic && (!Inlined(slot) || Name(slot.Class) == NullableClass));
        return kept ? slot : throw new InvalidOperationException($"{name} cannot be cleared");
    }

    internal static void Forget(Slot cache)
    {
        // A nullable keeps its flag in front of its value; with the flag down the game works the value out again.
        if (Inlined(cache))
            Marshal.WriteByte(cache.Address, 0);
        else
            Marshal.WriteIntPtr(cache.Address, IntPtr.Zero);
    }

    private static InvalidOperationException Misfit(PatchScalar value) =>
        new($"{value} does not fit what the game keeps here");

    private static Range Numbers(Slot slot) => wholes.TryGetValue(slot.Type, out var range)
        ? range
        : throw new InvalidOperationException("the game keeps more than one value here");

    private static long Whole(IntPtr address, Range range) => range.Size switch
    {
        1 => range.Low < 0 ? (sbyte)Marshal.ReadByte(address) : Marshal.ReadByte(address),
        2 => range.Low < 0 ? Marshal.ReadInt16(address) : (ushort)Marshal.ReadInt16(address),
        4 => range.Low < 0 ? Marshal.ReadInt32(address) : (uint)Marshal.ReadInt32(address),
        _ => Marshal.ReadInt64(address),
    };

    private static void Store(IntPtr address, int size, long whole)
    {
        switch (size)
        {
            case 1:
                Marshal.WriteByte(address, unchecked((byte)whole));
                break;
            case 2:
                Marshal.WriteInt16(address, unchecked((short)whole));
                break;
            case 4:
                Marshal.WriteInt32(address, unchecked((int)whole));
                break;
            default:
                Marshal.WriteInt64(address, whole);
                break;
        }
    }

    private static bool Inlined(Slot slot) =>
        slot.Type == Struct || (slot.Type == Generic && IL2CPP.il2cpp_class_is_valuetype(slot.Class));

    private static Sequence Spread(Value array)
    {
        if (!elements.TryGetValue(array.Class, out var element))
        {
            var kind = Kind(IL2CPP.il2cpp_class_get_type(IL2CPP.il2cpp_class_get_element_class(array.Class)));
            element = elements[array.Class] = kind with { Offset = IL2CPP.il2cpp_array_element_size(array.Class) };
        }
        var count = checked((int)IL2CPP.il2cpp_array_length(array.Data));
        return new Sequence(array.Data, count, element.Offset, element.Type, element.Class);
    }

    private static Shape ShapeOf(IntPtr type)
    {
        if (shapes.TryGetValue(type, out var known))
            return known;
        var listed = Name(type) == ListClass && Namespace(type) == Collections;
        var shape = (int)IL2CPP.il2cpp_type_get_type(IL2CPP.il2cpp_class_get_type(type)) == Vector ? Shape.Vector
            : listed ? Shape.List : Shape.Plain;
        return shapes[type] = shape;
    }

    private static Member Describe(IntPtr type, string name)
    {
        if (members.TryGetValue((type, name), out var known))
            return known;
        var field = IL2CPP.il2cpp_class_get_field_from_name(type, name);
        if (field == IntPtr.Zero || ((int)IL2CPP.il2cpp_field_get_flags(field) & StaticFlag) != 0)
            throw new MissingFieldException($"the game has no field named {name} here");
        var kind = Kind(IL2CPP.il2cpp_field_get_type(field));
        return members[(type, name)] = kind with { Offset = (int)IL2CPP.il2cpp_field_get_offset(field) };
    }

    private static Member Kind(IntPtr declared)
    {
        var type = (int)IL2CPP.il2cpp_type_get_type(declared);
        var described = IL2CPP.il2cpp_class_from_type(declared);
        // An enum is stored as the number underneath it.
        if (type == Struct && IL2CPP.il2cpp_class_is_enum(described))
            type = (int)IL2CPP.il2cpp_type_get_type(IL2CPP.il2cpp_class_enum_basetype(described));
        return new Member(0, type, described);
    }

    private static string? Name(IntPtr type) => Marshal.PtrToStringUTF8(IL2CPP.il2cpp_class_get_name(type));

    private static string? Namespace(IntPtr type) => Marshal.PtrToStringUTF8(IL2CPP.il2cpp_class_get_namespace(type));
}
