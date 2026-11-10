package nl.mediaguard.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import nl.mediaguard.app.data.LibraryFile
import nl.mediaguard.app.data.LibraryItem
import nl.mediaguard.app.data.LibraryPage
import nl.mediaguard.app.data.formatBytes
import nl.mediaguard.app.data.formatTime
import nl.mediaguard.app.ui.CardHeader
import nl.mediaguard.app.ui.EmptyState
import nl.mediaguard.app.ui.ErrorState
import nl.mediaguard.app.ui.ItemDetail
import nl.mediaguard.app.ui.KeyValue
import nl.mediaguard.app.ui.LibraryKind
import nl.mediaguard.app.ui.LoadingState
import nl.mediaguard.app.ui.MgCard
import nl.mediaguard.app.ui.MgSearchField
import nl.mediaguard.app.ui.MgSwitch
import nl.mediaguard.app.ui.PageHeader
import nl.mediaguard.app.ui.PeilLabel
import nl.mediaguard.app.ui.PeilIcon
import nl.mediaguard.app.ui.Kenmerk
import nl.mediaguard.app.ui.LabelRow
import nl.mediaguard.app.ui.Beschikbaarheid
import nl.mediaguard.app.ui.theme.TouchTarget
import nl.mediaguard.app.R
import androidx.annotation.DrawableRes
import androidx.compose.foundation.layout.heightIn
import nl.mediaguard.app.ui.MgButton
import nl.mediaguard.app.ui.ButtonKind
import nl.mediaguard.app.ui.ScreenList
import nl.mediaguard.app.ui.Segmented
import nl.mediaguard.app.ui.Timeline
import nl.mediaguard.app.ui.TimelineEntry
import nl.mediaguard.app.ui.UiState
import nl.mediaguard.app.ui.refreshing
import nl.mediaguard.app.ui.theme.Mg
import nl.mediaguard.app.ui.theme.MgSpace

// ---------------------------------------------------------------------------------- lijst

@Composable
fun LibraryContent(
    kind: LibraryKind,
    state: UiState<LibraryPage>,
    query: String,
    onQuery: (String) -> Unit,
    onSearch: () -> Unit,
    onKind: (LibraryKind) -> Unit,
    onOpen: (LibraryItem) -> Unit,
    onRefresh: () -> Unit,
    /** Alleen titels met ontbrekende afleveringen of films (vanaf de tegel "Ontbrekend"). */
    missingOnly: Boolean = false,
    onShowAll: () -> Unit = {},
) {
    ScreenList(refreshing = state.refreshing, onRefresh = onRefresh) {
        item {
            val total = (state as? UiState.Data)?.value?.total
            if (missingOnly) PageHeader("Ontbrekend", total?.let { "$it ${if (it == 1) "titel mist" else "titels missen"} afleveringen of films" }
                ?: "Titels die nog niet compleet zijn")
            else PageHeader("Bibliotheek", total?.let { "$it ${kind.label.lowercase()}" } ?: "Kwaliteit, seizoenen en upgrades")
        }
        if (missingOnly) item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                PeilLabel(Beschikbaarheid.ontbreekt("Alleen ontbrekend"))
                Spacer(Modifier.width(MgSpace.s2))
                MgButton("Alles tonen", onShowAll, kind = ButtonKind.Secondary, small = true)
            }
        }
        else item { Segmented(LibraryKind.entries.map { it.label }, kind.ordinal, { onKind(LibraryKind.entries[it]) }) }
        item { MgSearchField(query, onQuery, if (missingOnly) "Zoek in ontbrekend" else "Zoek in ${kind.label.lowercase()}", onSearch) }
        when (state) {
            UiState.Loading -> item { LoadingState(3) }
            is UiState.Error -> item { ErrorState(state.message, onRefresh) }
            is UiState.Data -> {
                val items = state.value.items
                if (items.isEmpty()) item {
                    MgCard { EmptyState(when {
                        query.isNotBlank() -> "Niets gevonden"
                        missingOnly -> "Niets ontbreekt"
                        else -> "Nog niets in deze bibliotheek"
                    }, if (query.isBlank()) null else "Probeer een andere titel.", icon = R.drawable.lucide_search_x) }
                }
                else item {
                    MgCard(padding = androidx.compose.foundation.layout.PaddingValues(horizontal = MgSpace.s3, vertical = MgSpace.s1)) {
                        Column { items.forEachIndexed { i, it -> LibraryRow(it, i > 0) { onOpen(it) } } }
                    }
                }
            }
        }
    }
}

@Composable
private fun LibraryRow(item: LibraryItem, divider: Boolean, onClick: () -> Unit) {
    Column(Modifier.fillMaxWidth()) {
        if (divider) Box(Modifier.fillMaxWidth().height(1.dp).background(Mg.colors.lineSoft))
        Row(Modifier.fillMaxWidth().heightIn(min = TouchTarget).clickable(onClick = onClick).padding(vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Poster(item.posterUrl, 44.dp, if (item.kind == "movie") R.drawable.lucide_film else R.drawable.lucide_tv)
            Spacer(Modifier.width(MgSpace.s3))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(item.title + (item.year?.let { " ($it)" } ?: ""), style = Mg.type.body.copy(fontWeight = FontWeight.Medium),
                    color = Mg.colors.text)
                Text(listOfNotNull(item.instanceName, item.sizeOnDisk.takeIf { it > 0 }?.let { formatBytes(it) },
                    item.stats?.takeIf { item.kind == "series" && it.total > 0 }?.let { "${it.available} van ${it.total} afl." })
                    .joinToString(" · "), style = Mg.type.meta, color = Mg.colors.muted)
                LabelRow(qualityChips(item))
            }
        }
    }
}

/**
 * Dezelfde labels als de web-bibliotheek: compleet / ontbreekt / kan beter / verwacht / niet gevolgd. Beschikbaarheid
 * is geen gezondheid: neutrale labels met een eigen icoon; ontbreken is neutraal-sterk (ADR-0016 punt 6).
 */
fun qualityChips(item: LibraryItem): List<Kenmerk> {
    val s = item.stats ?: return emptyList()
    val out = mutableListOf<Kenmerk>()
    when {
        item.monitored == false -> out += Beschikbaarheid.nietGevolgd()
        s.missing > 0 -> out += Beschikbaarheid.ontbreekt(if (item.kind == "movie") "Ontbreekt" else "${s.missing} ontbreken")
        s.total > 0 && s.available >= s.total - s.upcoming - s.unmonitored -> out += Beschikbaarheid.compleet()
    }
    if (s.cutoffUnmet > 0) out += Beschikbaarheid.kanBeter()
    if (s.upcoming > 0 && item.monitored != false) out += Beschikbaarheid.verwacht()
    return out.take(2)
}

/** Poster met een plaatshouder (icoon) zolang er geen afbeelding is; hoeken 6 dp, rand `border`. */
@Composable
fun Poster(url: String?, width: androidx.compose.ui.unit.Dp, @DrawableRes fallback: Int) {
    val shape = RoundedCornerShape(6.dp)
    Box(Modifier.width(width).height(width * 1.5f).clip(shape).background(Mg.peil.muted).border(1.dp, Mg.colors.line, shape),
        contentAlignment = Alignment.Center) {
        PeilIcon(fallback, Mg.colors.muted, size = width / 2)
        if (!url.isNullOrBlank()) AsyncImage(url, null, contentScale = ContentScale.Crop, modifier = Modifier.matchParentSize())
    }
}

// ---------------------------------------------------------------------------------- detail

@Composable
fun LibraryItemContent(title: String?, state: UiState<ItemDetail>, onSeason: (Int, Boolean) -> Unit, onRefresh: () -> Unit) {
    ScreenList(refreshing = state.refreshing, onRefresh = onRefresh) {
        when (state) {
            UiState.Loading -> item { LoadingState(3) }
            is UiState.Error -> item { ErrorState(state.message, onRefresh) }
            is UiState.Data -> {
                val d = state.value
                item {
                    PageHeader(d.seasons?.title ?: title ?: "Titel",
                        listOfNotNull(if (d.files.kind == "series") "Serie" else "Film", d.files.profile.name).joinToString(" · "))
                }
                d.seasons?.takeIf { it.seasons.isNotEmpty() }?.let { m ->
                    item {
                        MgCard {
                            CardHeader("Seizoenen", R.drawable.lucide_list, count = m.seasons.size)
                            m.seasons.forEachIndexed { i, season ->
                                if (i > 0) Box(Modifier.fillMaxWidth().height(1.dp).background(Mg.colors.lineSoft))
                                val have = season.episodes.count { it.hasFile }
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Column(Modifier.weight(1f)) {
                                        Text(if (season.seasonNumber == 0) "Specials" else "Seizoen ${season.seasonNumber}",
                                            style = Mg.type.body.copy(fontWeight = FontWeight.Medium), color = Mg.colors.text)
                                        Text("$have van ${season.episodes.size} aanwezig" + if (season.monitored) " · gevolgd" else "",
                                            style = Mg.type.meta, color = Mg.colors.muted)
                                    }
                                    MgSwitch(season.monitored, { onSeason(season.seasonNumber, it) })
                                }
                            }
                            Text("Aanzetten vraagt of MediaGuard meteen moet zoeken.", style = Mg.type.meta, color = Mg.colors.muted)
                        }
                    }
                }
                item { QualityPanel(d) }
                item {
                    MgCard {
                        CardHeader("Upgrades", R.drawable.lucide_trending_up, count = d.upgrades.upgrades.size)
                        if (d.upgrades.upgrades.isEmpty()) EmptyState("Geen upgrades in de laatste 90 dagen")
                        else Timeline(d.upgrades.upgrades.map { u ->
                            TimelineEntry(
                                u.reason.ifBlank { "Upgrade" },
                                listOfNotNull(
                                    u.label,
                                    "${u.old.quality ?: "?"} (${u.old.score ?: "?"}) → ${u.new.quality ?: "?"} (${u.new.score ?: "?"})",
                                    u.source?.let { "via $it" },
                                    if (u.old.av1ByMediaGuard) "verving een AV1-versie" else null,
                                ).joinToString(" · "),
                                formatTime(u.at),
                            )
                        })
                    }
                }
            }
        }
    }
}

@Composable
private fun QualityPanel(d: ItemDetail) {
    MgCard {
        CardHeader("Kwaliteit", R.drawable.lucide_gauge, count = d.files.files.size.takeIf { it > 1 })
        val p = d.files.profile
        val files = d.files.files
        if (files.isEmpty()) { EmptyState("Nog geen bestanden"); return@MgCard }
        if (files.size == 1) {
            val f = files.first()
            KeyValue("Kwaliteit", f.quality.name ?: "—")
            KeyValue("Beeld", listOfNotNull(f.media.videoCodec, f.media.dynamicRange, f.size?.let { formatBytes(it) }).joinToString(" · ").ifBlank { "—" })
            KeyValue("Audio", f.media.audioLanguages.joinToString(", ").ifBlank { "—" })
            KeyValue("Ondertitels", f.media.subtitleLanguages.joinToString(", ").ifBlank { "—" })
            KeyValue("Custom formats", f.customFormats.joinToString(", ") { "${it.name.removePrefix("MediaGuard · ")} (${it.score})" }.ifBlank { "—" })
            KeyValue("Score", listOfNotNull(f.score?.toString(), p.cutoffScore?.let { "doel $it" }).joinToString(" · ").ifBlank { "—" })
            LabelRow(fileChips(f))
        } else {
            KeyValue("Profiel", p.name ?: "—")
            KeyValue("Doel", listOfNotNull(p.cutoff, p.cutoffScore?.let { "score $it" }).joinToString(" · ").ifBlank { "—" })
            val met = files.count { it.cutoffMet == true }
            PeilLabel(if (met == files.size) Beschikbaarheid.doelBereikt() else Kenmerk("$met van ${files.size} op doel", R.drawable.lucide_trending_up))
            files.take(150).forEachIndexed { i, f ->
                if (i > 0) Box(Modifier.fillMaxWidth().height(1.dp).background(Mg.colors.lineSoft))
                Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text(f.label ?: "Bestand", style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold), color = Mg.colors.text)
                    LabelRow(fileChips(f))
                    Text(f.summary ?: listOfNotNull(f.quality.name, f.media.videoCodec, f.media.dynamicRange,
                        f.size?.let { formatBytes(it) }).joinToString(" · "), style = Mg.type.meta, color = Mg.colors.muted)
                }
            }
        }
    }
}

/** Labels per bestand: AV1 door MediaGuard (neutraal, kwaliteit is geen status) en of het doel bereikt is. */
fun fileChips(f: LibraryFile): List<Kenmerk> = buildList {
    if (f.av1ByMediaGuard) add(Kenmerk("AV1 door MediaGuard", R.drawable.lucide_zap))
    f.cutoffMet?.let { met -> add(if (met) Beschikbaarheid.doelBereikt() else Beschikbaarheid.kanBeter()) }
}
