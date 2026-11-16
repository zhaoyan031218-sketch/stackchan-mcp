# Called by configure_robot.py. Only a successfully verified trust anchor is output.
$ErrorActionPreference = "Stop"
Add-Type -TypeDefinition @'
using System;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Cryptography.X509Certificates;

public static class StackchanVerifiedRoot {
    public static string Fetch(string serverName) {
        byte[] anchor = null;
        using (var tcp = new TcpClient()) {
            var connecting = tcp.ConnectAsync(serverName, 443);
            if (!connecting.Wait(10000)) throw new TimeoutException("TCP connection timed out");
            connecting.GetAwaiter().GetResult();
            using (var tls = new SslStream(tcp.GetStream(), false,
                (sender, certificate, chain, errors) => {
                    // Retain OS chain, expiry and hostname validation. Never accept an error.
                    if (errors != SslPolicyErrors.None || chain == null ||
                        chain.ChainElements.Count < 2) return false;
                    anchor = chain.ChainElements[chain.ChainElements.Count - 1].Certificate.RawData;
                    return true;
                })) {
                tls.ReadTimeout = 10000;
                tls.WriteTimeout = 10000;
                tls.AuthenticateAsClient(serverName);
                if (anchor == null) throw new InvalidOperationException("No verified trust anchor");
                return "-----BEGIN CERTIFICATE-----\n" +
                    Convert.ToBase64String(anchor, Base64FormattingOptions.InsertLineBreaks) +
                    "\n-----END CERTIFICATE-----\n";
            }
        }
    }
}
'@
[StackchanVerifiedRoot]::Fetch($env:STACKCHAN_CERT_HOST)
