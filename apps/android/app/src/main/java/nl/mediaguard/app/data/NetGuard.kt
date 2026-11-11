package nl.mediaguard.app.data

import okhttp3.Dns
import okhttp3.Interceptor
import okhttp3.Response
import java.io.IOException
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.Proxy

/** Onversleuteld (http://) verkeer naar een adres buiten het eigen netwerk is geweigerd. */
class CleartextBlocked(val host: String) :
    IOException(tr("Onversleuteld verkeer naar $host is geblokkeerd: gebruik https:// buiten je thuisnetwerk.", "Unencrypted traffic to $host is blocked: use https:// outside your home network."))

/**
 * http:// mag alleen naar adressen in het eigen netwerk (LAN, VPN, deze telefoon). De API-sleutel gaat
 * zo nooit onversleuteld over internet, ook niet als een koppelcode per ongeluk http:// bevat.
 * Android kan dit niet per IP-bereik in network_security_config uitdrukken; daarom controleert de app
 * vóór elk http-verzoek waar de naam naartoe wijst. https:// gaat altijd door.
 *
 * Gebruik hem twee keer ([ApiClient.guarded]): als gewone interceptor (vóór er verbinding is) en als
 * netwerk-interceptor. Die laatste ziet ook elke doorverwijzing (OkHttp neemt X-Api-Key mee naar een
 * ander adres) en controleert het adres waarmee echt verbonden is, vóórdat er iets verstuurd wordt.
 */
class CleartextGuard(
    private val dns: Dns = Dns.SYSTEM,
    private val isLocal: (InetAddress) -> Boolean = NetGuard::isLocalAddress,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        if (!request.isHttps) {
            val host = request.url.host
            val route = chain.connection()?.route()
            val connected = route?.takeIf { it.proxy.type() == Proxy.Type.DIRECT }?.socketAddress?.address
            val addresses = if (connected != null) listOf(connected) else dns.lookup(host)
            if (addresses.isEmpty() || addresses.any { !isLocal(it) }) throw CleartextBlocked(host)
        }
        return chain.proceed(request)
    }
}

object NetGuard {
    /** Loopback, privé-LAN (10/8, 172.16/12, 192.168/16), link-local, CGNAT/Tailscale (100.64/10) en IPv6 ULA (fc00::/7). */
    fun isLocalAddress(a: InetAddress): Boolean {
        if (a.isLoopbackAddress || a.isSiteLocalAddress || a.isLinkLocalAddress || a.isAnyLocalAddress) return true
        val b = a.address
        return when (a) {
            is Inet4Address -> (b[0].toInt() and 0xff) == 100 && (b[1].toInt() and 0xC0) == 64
            is Inet6Address -> (b[0].toInt() and 0xfe) == 0xfc
            else -> false
        }
    }
}
