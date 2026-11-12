package nl.mediaguard.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import nl.mediaguard.app.data.MyRequest
import nl.mediaguard.app.data.MyRequests
import nl.mediaguard.app.data.SeerrDetail
import nl.mediaguard.app.data.SeerrPage
import nl.mediaguard.app.data.SeerrResult
import nl.mediaguard.app.data.formatTime
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.ui.ButtonKind
import nl.mediaguard.app.ui.CardHeader
import nl.mediaguard.app.ui.EmptyState
import nl.mediaguard.app.ui.ErrorState
import nl.mediaguard.app.ui.FilterChips
import nl.mediaguard.app.ui.LoadingState
import nl.mediaguard.app.ui.MgButton
import nl.mediaguard.app.ui.MgCard
import nl.mediaguard.app.ui.MgProgress
import nl.mediaguard.app.ui.MgSearchField
import nl.mediaguard.app.ui.MgSwitch
import nl.mediaguard.app.ui.PageHeader
import nl.mediaguard.app.ui.PeilLabel
import nl.mediaguard.app.ui.PeilIcon
import nl.mediaguard.app.ui.Kenmerk
import nl.mediaguard.app.ui.theme.MgShape
import nl.mediaguard.app.ui.theme.TouchTarget
import nl.mediaguard.app.R
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.heightIn
import nl.mediaguard.app.ui.ScreenList
import nl.mediaguard.app.ui.SectionLabel
import nl.mediaguard.app.ui.SkeletonLine
import nl.mediaguard.app.ui.Tone
import nl.mediaguard.app.ui.UiState
import nl.mediaguard.app.ui.refreshing
import nl.mediaguard.app.ui.theme.Mg
import nl.mediaguard.app.ui.theme.MgSpace

enum class RequestsTab(private val nl: String, private val en: String) {
    Ontdekken("Ontdekken", "Discover"), Mijn("Mijn aanvragen", "My requests");

    val label: String get() = tr(nl, en)
}

/**
 * Seerr-status → label. Beschikbaar of aangevraagd is geen gezondheid: neutrale labels met een eigen icoon,
 * gedeeltelijk = neutraal-sterk (er ontbreekt iets). Zonder bekende status geen label.
 *
 * MediaGuard geeft de tekst alleen in het Nederlands (`status_nl`). In het Engels kiest de app daarom zelf het
 * woord bij een bekende status; een onbekende status toont de tekst van de server ongewijzigd (ADR-0023).
 */
fun seerrLabel(status: String?, text: String?): Kenmerk? {
    val t = text?.ifBlank { null } ?: status?.ifBlank { null } ?: return null
    return when (status) {
        "beschikbaar" -> Kenmerk(tr(t, "Available"), R.drawable.lucide_check)
        "aangevraagd" -> Kenmerk(tr(t, "Requested"), R.drawable.lucide_inbox)
        "verwerken" -> Kenmerk(tr(t, "Processing"), R.drawable.lucide_loader_circle)
        "gedeeltelijk" -> Kenmerk(tr(t, "Partially available"), R.drawable.lucide_file_x, Tone.Strong)
        else -> Kenmerk(t, R.drawable.lucide_info)
    }
}

class RequestsActions(
    val selectTab: (RequestsTab) -> Unit = {},
    val query: (String) -> Unit = {},
    val search: () -> Unit = {},
    val open: (String, Long) -> Unit = { _, _ -> },
    val refreshMine: () -> Unit = {},
)

@Composable
fun RequestsContent(tab: RequestsTab, q: String, results: UiState<SeerrPage>, mine: UiState<MyRequests>, a: RequestsActions) {
    ScreenList(refreshing = if (tab == RequestsTab.Mijn) mine.refreshing else results.refreshing,
        onRefresh = if (tab == RequestsTab.Mijn) a.refreshMine else a.search) {
        item { PageHeader(tr("Aanvragen", "Requests"), tr("Vraag films en series aan via Seerr; MediaGuard levert ze volgens je eisen.", "Request movies and series through Seerr; MediaGuard delivers them according to your requirements.")) }
        item { FilterChips(RequestsTab.entries.map { it to it.label }, tab, a.selectTab) }
        if (tab == RequestsTab.Ontdekken) {
            item { MgSearchField(q, a.query, tr("Film of serie zoeken", "Search movie or series"), a.search) }
            when (results) {
                UiState.Loading -> item { LoadingState(2) }
                is UiState.Error -> item { ErrorState(results.message, a.search) }
                is UiState.Data -> {
                    item { SectionLabel(if (q.isBlank()) tr("Populair nu", "Trending") else tr("Resultaten", "Results")) }
                    if (results.value.results.isEmpty()) item { MgCard { EmptyState(tr("Niets gevonden", "Nothing found"), tr("Probeer een andere titel.", "Try another title.")) } }
                    results.value.results.chunked(2).forEach { row ->
                        item(key = row.joinToString { "${it.mediaType}-${it.tmdbId}" }) {
                            Row(Modifier.height(IntrinsicSize.Max), horizontalArrangement = Arrangement.spacedBy(MgSpace.s3)) {
                                row.forEach { r -> PosterCard(r, Modifier.weight(1f).fillMaxHeight()) { a.open(r.mediaType, r.tmdbId) } }
                                if (row.size == 1) Spacer(Modifier.weight(1f))
                            }
                        }
                    }
                }
            }
        } else {
            when (mine) {
                UiState.Loading -> item { LoadingState(2) }
                is UiState.Error -> item { ErrorState(mine.message, a.refreshMine) }
                is UiState.Data -> item {
                    MgCard {
                        CardHeader(RequestsTab.Mijn.label, R.drawable.lucide_inbox, count = mine.value.results.size)
                        if (mine.value.results.isEmpty()) EmptyState(tr("Nog geen aanvragen", "No requests yet"), tr("Zoek bij Ontdekken een film of serie.", "Find a movie or series under Discover."))
                        mine.value.results.forEachIndexed { i, r -> MyRequestRow(r, i > 0) { a.open(r.mediaType, r.tmdbId) } }
                    }
                }
            }
        }
    }
}

/** Posterkaart: poster 2:3 met plaatshouder, titel (loopt om), status als neutraal label, één knop van 48 dp. */
@Composable
private fun PosterCard(r: SeerrResult, modifier: Modifier, onClick: () -> Unit) {
    MgCard(modifier, onClick = onClick, padding = androidx.compose.foundation.layout.PaddingValues(0.dp)) {
        Column(Modifier.fillMaxHeight()) {
            Box(Modifier.fillMaxWidth().aspectRatio(2f / 3f).background(Mg.peil.muted), contentAlignment = Alignment.Center) {
                PeilIcon(if (r.mediaType == "tv") R.drawable.lucide_tv else R.drawable.lucide_film, Mg.colors.muted, size = 32.dp)
                if (!r.posterUrl.isNullOrBlank()) AsyncImage(r.posterUrl, null, contentScale = ContentScale.Crop,
                    modifier = Modifier.matchParentSize())
                if (r.status != null && r.status != "niet") seerrLabel(r.status, r.statusNl)?.let { k ->
                    Box(Modifier.align(Alignment.TopStart).padding(6.dp)) { PeilLabel(k, background = Mg.peil.card) }
                }
            }
            Column(Modifier.weight(1f).padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(r.title, style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold), color = Mg.colors.text)
                Text(listOfNotNull(r.year?.toString(), if (r.mediaType == "tv") tr("Serie", "Series") else tr("Film", "Movie")).joinToString(" · "),
                    style = Mg.type.meta, color = Mg.colors.muted)
                Spacer(Modifier.weight(1f))
                MgButton(if (r.status == null || r.status == "niet" || r.status == "gedeeltelijk") tr("Aanvragen", "Request") else tr("Bekijken", "View"), onClick,
                    kind = if (r.status == null || r.status == "niet") ButtonKind.Primary else ButtonKind.Secondary,
                    small = true, modifier = Modifier.fillMaxWidth())
            }
        }
    }
}

@Composable
private fun MyRequestRow(r: MyRequest, divider: Boolean, onClick: () -> Unit) {
    Column(Modifier.fillMaxWidth()) {
        if (divider) Box(Modifier.fillMaxWidth().height(1.dp).background(Mg.colors.lineSoft))
        Row(Modifier.fillMaxWidth().heightIn(min = TouchTarget).clickable(onClick = onClick).padding(vertical = 12.dp)) {
            Poster(r.posterUrl, 44.dp, if (r.mediaType == "tv") R.drawable.lucide_tv else R.drawable.lucide_film)
            Spacer(Modifier.width(MgSpace.s3))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(r.title + (r.year?.let { " ($it)" } ?: ""), style = Mg.type.body.copy(fontWeight = FontWeight.Medium),
                    color = Mg.colors.text)
                Text(listOfNotNull(r.seerrStatus, formatTime(r.requestedAt).ifBlank { null }, r.requestedBy).joinToString(" · "),
                    style = Mg.type.meta, color = Mg.colors.muted)
                r.delivery?.let { d ->
                    PeilLabel(deliveryLabel(d.state, d.stateNl ?: d.state ?: ""))
                    d.progress?.takeIf { it.total > 0 }?.let { p ->
                        MgProgress(p.present.toFloat() / p.total, color = Mg.peil.chart1, height = 6.dp,
                            description = tr("${p.present} van ${p.total} aanwezig", "${p.present} of ${p.total} present"))
                        Text(tr("${p.present} van ${p.total} aanwezig", "${p.present} of ${p.total} present"), style = Mg.type.meta, color = Mg.colors.muted)
                    }
                    d.nextStep?.let { Text(it, style = Mg.type.meta, color = Mg.colors.text) }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------------- aanvraagvenster

/** Inhoud van het aanvraagvenster (bottom sheet): poster, info, seizoenschakelaars, één knop. */
@Composable
fun RequestSheetContent(state: UiState<SeerrDetail>, chosen: SnapshotStateList<Int>, onRequest: (List<Int>?) -> Unit, onRetry: () -> Unit) {
    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = MgSpace.s4).padding(top = MgSpace.s2, bottom = MgSpace.s5),
        verticalArrangement = Arrangement.spacedBy(MgSpace.s3)) {
        when (state) {
            UiState.Loading -> { SkeletonLine(0.6f, 18.dp); SkeletonLine(0.9f); SkeletonLine(0.8f) }
            is UiState.Error -> ErrorState(state.message, onRetry)
            is UiState.Data -> {
                val d = state.value
                Row {
                    val shape = RoundedCornerShape(8.dp)
                    Box(Modifier.width(96.dp).aspectRatio(2f / 3f).clip(shape).background(Mg.peil.muted).border(1.dp, Mg.colors.line, shape)) {
                        if (!d.posterUrl.isNullOrBlank()) AsyncImage(d.posterUrl, null, contentScale = ContentScale.Crop, modifier = Modifier.matchParentSize())
                    }
                    Spacer(Modifier.width(MgSpace.s3))
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(d.title, style = Mg.type.section, color = Mg.colors.text)
                        Text(listOfNotNull(d.year?.toString(), if (d.mediaType == "tv") tr("Serie", "Series") else tr("Film", "Movie"), d.runtime?.let { "$it min" },
                            d.rating?.let { "★ %.1f".format(it) }).joinToString(" · "), style = Mg.type.meta, color = Mg.colors.muted)
                        d.statusNl?.let { seerrLabel(d.status, it)?.let { k -> PeilLabel(k) } }
                        if (d.genres.isNotEmpty()) Text(d.genres.joinToString(", "), style = Mg.type.meta, color = Mg.colors.muted)
                    }
                }
                d.overview?.let { Text(it, style = Mg.type.body, color = Mg.colors.text) }
                if (d.mediaType == "tv" && d.seasons.isNotEmpty()) {
                    MgCard {
                        CardHeader(tr("Seizoenen", "Seasons"), trailing = { Text(if (chosen.isEmpty()) tr("alles", "all") else tr("${chosen.size} gekozen", "${chosen.size} selected"),
                            style = Mg.type.meta, color = Mg.colors.muted) })
                        d.seasons.forEachIndexed { i, season ->
                            val available = season.status == "beschikbaar"
                            if (i > 0) Box(Modifier.fillMaxWidth().height(1.dp).background(Mg.colors.lineSoft))
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text(tr("Seizoen ${season.seasonNumber}", "Season ${season.seasonNumber}") + (season.episodes?.let { tr(" · $it afl.", " · $it ep.") } ?: ""),
                                        style = Mg.type.body.copy(fontWeight = FontWeight.Medium), color = Mg.colors.text)
                                    season.statusNl?.let { Text(it, style = Mg.type.meta, color = Mg.colors.muted) }
                                }
                                MgSwitch(season.seasonNumber in chosen || available, { on ->
                                    if (on) chosen.add(season.seasonNumber) else chosen.remove(season.seasonNumber)
                                }, enabled = !available)
                            }
                        }
                    }
                }
                val canRequest = d.status !in setOf("beschikbaar", "aangevraagd", "verwerken") || d.mediaType == "tv"
                MgButton(when {
                    d.mediaType != "tv" -> tr("Aanvragen", "Request")
                    chosen.isEmpty() -> tr("Alle seizoenen aanvragen", "Request all seasons")
                    chosen.size == 1 -> tr("1 seizoen aanvragen", "Request 1 season")
                    else -> tr("${chosen.size} seizoenen aanvragen", "Request ${chosen.size} seasons")
                }, { onRequest(if (d.mediaType == "tv") chosen.toList().ifEmpty { null } else null) },
                    kind = ButtonKind.Primary, enabled = canRequest, modifier = Modifier.fillMaxWidth())
                Text(tr("MediaGuard levert het daarna vanzelf volgens je eisen.", "MediaGuard then delivers it by itself according to your requirements."), style = Mg.type.meta, color = Mg.colors.muted)
            }
        }
    }
}

/**
 * Leverstatus als label; de staten van MediaGuard zijn active, waiting_release, delivered, not_found en stopped
 * (niet meer gevolgd: knop Stop, verwijderd in Seerr of uit Sonarr/Radarr). Geleverd is geen gezondheid (neutraal
 * vinkje); niet gevonden is neutraal-sterk; vast of mislukt is een gemeten falen (Storing-vorm).
 */
fun deliveryLabel(state: String?, text: String): Kenmerk = when (state) {
    "delivered", "geleverd", "beschikbaar", "compleet" -> Kenmerk(text, R.drawable.lucide_check)
    "not_found" -> Kenmerk(text, R.drawable.lucide_search_x, Tone.Strong)
    "vast", "mislukt", "failed" -> Kenmerk(text, R.drawable.lucide_octagon_x, Tone.Bad)
    "waiting_release" -> Kenmerk(text, R.drawable.lucide_calendar_clock)
    "stopped" -> Kenmerk(text, R.drawable.lucide_eye_off)
    else -> Kenmerk(text, R.drawable.lucide_loader_circle)
}
