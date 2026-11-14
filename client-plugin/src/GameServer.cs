namespace MonmusuTd.Local;

// Reads what the local game server prepares for the game apart from its API.
internal sealed class GameServer
{
    // A system proxy must not sit between the game and its local server.
    private readonly HttpClient http = new(new SocketsHttpHandler { UseProxy = false })
    {
        // The master patch carries every translated text and is tens of megabytes long.
        Timeout = TimeSpan.FromSeconds(30),
    };
    private readonly EndpointAddress address;

    internal GameServer(EndpointAddress address) => this.address = address;

    internal string Read(string route)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, address.Origin + route);
        using var response = http.Send(request);
        response.EnsureSuccessStatusCode();
        using var reader = new StreamReader(response.Content.ReadAsStream());
        return reader.ReadToEnd();
    }
}
