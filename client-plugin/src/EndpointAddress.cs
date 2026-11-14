namespace MonmusuTd.Local;

internal sealed class EndpointAddress
{
    internal string Origin { get; }

    internal EndpointAddress(string baseUrl)
    {
        var origin = new Uri(baseUrl, UriKind.Absolute);
        if (origin.Scheme is not ("http" or "https") || origin.AbsolutePath != "/"
            || origin.Query.Length != 0 || origin.Fragment.Length != 0 || origin.UserInfo.Length != 0)
            throw new ArgumentException("BaseUrl must be an HTTP(S) origin without a path, query or credentials.", nameof(baseUrl));
        Origin = origin.GetLeftPart(UriPartial.Authority);
    }

    internal string Redirect(string url) => Origin + new Uri(url, UriKind.Absolute).PathAndQuery;
}
