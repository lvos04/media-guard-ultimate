package nl.mediaguard.app.data

import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.io.IOException
import java.util.concurrent.TimeUnit

val MgJson = Json {
    ignoreUnknownKeys = true
    coerceInputValues = true
    explicitNulls = false
    isLenient = true
}

/** Terugmeldingen van elke API-aanroep: MediaGuard antwoordde, of weigerde de sleutel (401). */
class ApiHooks(val onContact: () -> Unit = {}, val onUnauthorized: () -> Unit = {})

object ApiClient {
    /** OkHttp met de http-bewaking (alleen https buiten het eigen netwerk, ook na een doorverwijzing);
     *  ook voor ntfy. */
    fun guarded(client: OkHttpClient? = null): OkHttpClient {
        val dns = client?.dns ?: okhttp3.Dns.SYSTEM
        return (client ?: OkHttpClient()).newBuilder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .addInterceptor(CleartextGuard(dns))
            .addNetworkInterceptor(CleartextGuard(dns))
            .build()
    }

    fun create(baseUrl: String, apiKey: String, client: OkHttpClient? = null, hooks: ApiHooks? = null): MediaGuardApi {
        val http = guarded(client).newBuilder()
            .addInterceptor { chain ->
                val response = chain.proceed(
                    chain.request().newBuilder()
                        .header("X-Api-Key", apiKey)
                        .header("Accept", "application/json")
                        .header("User-Agent", "MediaGuard-Android")
                        .build(),
                )
                if (hooks != null) {
                    when {
                        response.code == 401 -> hooks.onUnauthorized()
                        response.code < 500 -> hooks.onContact()
                    }
                }
                response
            }
            .build()
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(http)
            .addConverterFactory(MgJson.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(MediaGuardApi::class.java)
    }
}

/** 401 van MediaGuard: de sleutel is ingetrokken, verlopen of onbekend. */
fun Throwable.isUnauthorized(): Boolean = this is HttpException && code() == 401

/** Fout in gewone taal voor de UI; leest {"detail": "..."} of {"detail": {"message": "..."}}. */
fun Throwable.friendlyMessage(): String = when (this) {
    is HttpException -> {
        val body = runCatching { response()?.errorBody()?.string() }.getOrNull()
        val detail = body?.let { b ->
            runCatching {
                val el = MgJson.parseToJsonElement(b)
                val d = (el as? kotlinx.serialization.json.JsonObject)?.get("detail")
                when (d) {
                    is kotlinx.serialization.json.JsonPrimitive -> d.content
                    is kotlinx.serialization.json.JsonObject ->
                        (d["message"] as? kotlinx.serialization.json.JsonPrimitive)?.content
                    else -> null
                }
            }.getOrNull()
        }
        when {
            !detail.isNullOrBlank() -> detail
            code() == 401 -> "API-sleutel geweigerd. Koppel de app opnieuw."
            code() == 403 -> "Deze API-sleutel mag dit niet (rol operator nodig)."
            code() == 404 -> "Deze MediaGuard-versie kent deze functie nog niet."
            else -> "MediaGuard gaf fout ${code()}."
        }
    }
    is CleartextBlocked -> "Onversleuteld (http://) naar een adres buiten je thuisnetwerk is geblokkeerd. " +
        "Gebruik het https-adres, bijvoorbeeld je Cloudflare-tunnel."
    is IOException -> "MediaGuard is niet bereikbaar. Controleer het adres en je verbinding."
    else -> message ?: "Onbekende fout"
}
