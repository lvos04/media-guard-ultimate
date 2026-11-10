package nl.mediaguard.app.data

import kotlinx.serialization.json.JsonObject
import okhttp3.ResponseBody
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

/** MediaGuard-API (zie docs/APP_API.md). Alle paden onder /api/v1. */
interface MediaGuardApi {
    @GET("api/v1/auth/me") suspend fun me(): Me

    @GET("api/v1/app/status") suspend fun status(): AppStatus
    @GET("api/v1/app/summary") suspend fun summary(@Query("date") date: String? = null): DailySummary
    @POST("api/v1/app/control") suspend fun control(@Body body: ControlRequest): ControlResult
    /** Rollend venster "Laatste 24 uur" (MediaGuard 1.2.2+; ouder: 404). */
    @GET("api/v1/app/recent") suspend fun recent(): DailySummary
    /** Na het koppelen: MediaGuard zet het telefoonkanaal (ntfy) aan. */
    @POST("api/v1/app/paired") suspend fun paired(@Body body: PairedBody): PairedResult
    /** Knop uit "Wacht op jou": hetzelfde endpoint als in de web. [path] zonder voorloop-slash. */
    @POST("api/v1/{path}") suspend fun action(@Path(value = "path", encoded = true) path: String, @Body body: JsonObject): ResponseBody
    /** Updatecontrole van de server (als die er is; vorm kan per versie verschillen). */
    @GET("api/v1/system/update") suspend fun systemUpdate(): ResponseBody

    @GET("api/v1/seerr/search") suspend fun seerrSearch(@Query("q") q: String, @Query("page") page: Int = 1): SeerrPage
    @GET("api/v1/seerr/discover/trending") suspend fun seerrTrending(@Query("page") page: Int = 1): SeerrPage
    @GET("api/v1/seerr/media/{type}/{id}") suspend fun seerrDetail(@Path("type") type: String, @Path("id") id: Long): SeerrDetail
    @POST("api/v1/seerr/request") suspend fun seerrRequest(@Body body: SeerrRequestBody): SeerrRequestResult
    @GET("api/v1/seerr/my-requests") suspend fun myRequests(@Query("filter") filter: String = "all", @Query("take") take: Int = 50): MyRequests

    @GET("api/v1/library") suspend fun library(
        @Query("q") q: String? = null,
        @Query("kind") kind: String? = null,
        @Query("media_type") mediaType: String? = null,
        @Query("state") state: String? = null,
        @Query("page_size") pageSize: Int = 30,
    ): LibraryPage
    @GET("api/v1/library/{id}/files") suspend fun libraryFiles(@Path("id") id: Long): LibraryFiles
    @GET("api/v1/library/{id}/upgrades") suspend fun libraryUpgrades(@Path("id") id: Long, @Query("days") days: Int = 90): UpgradeHistory
    @GET("api/v1/library/{id}/seasons") suspend fun librarySeasons(@Path("id") id: Long): SeasonMatrix
    @PUT("api/v1/library/{id}/seasons/{season}") suspend fun setSeasonMonitored(
        @Path("id") id: Long, @Path("season") season: Int, @Body body: MonitorBody,
    ): MonitorResult
}
