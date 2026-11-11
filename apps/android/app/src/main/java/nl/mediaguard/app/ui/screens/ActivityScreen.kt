package nl.mediaguard.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import nl.mediaguard.app.data.AppStatus
import nl.mediaguard.app.data.DailySummary
import nl.mediaguard.app.data.DownloadItem
import nl.mediaguard.app.data.RunningEncode
import nl.mediaguard.app.data.formatBytes
import nl.mediaguard.app.data.formatClock
import nl.mediaguard.app.data.formatDay
import nl.mediaguard.app.data.formatEta
import nl.mediaguard.app.data.formatTime
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.ui.Banner
import nl.mediaguard.app.ui.ButtonKind
import nl.mediaguard.app.ui.CardHeader
import nl.mediaguard.app.ui.EmptyState
import nl.mediaguard.app.ui.ErrorState
import nl.mediaguard.app.ui.FilterChips
import nl.mediaguard.app.ui.KpiTile
import nl.mediaguard.app.ui.ListRow
import nl.mediaguard.app.ui.LoadingState
import nl.mediaguard.app.ui.MgButton
import nl.mediaguard.app.ui.MgCard
import nl.mediaguard.app.ui.MgProgress
import nl.mediaguard.app.ui.PageHeader
import nl.mediaguard.app.ui.PeilLabel
import nl.mediaguard.app.ui.PeilIcon
import nl.mediaguard.app.ui.Kenmerk
import nl.mediaguard.app.ui.Baan
import nl.mediaguard.app.ui.AttentionKind
import nl.mediaguard.app.ui.PeilStatus
import nl.mediaguard.app.ui.IconBubble
import nl.mediaguard.app.ui.Melding
import nl.mediaguard.app.R
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.heightIn
import androidx.compose.ui.graphics.Color
import nl.mediaguard.app.ui.ScreenList
import nl.mediaguard.app.ui.TileRow
import nl.mediaguard.app.ui.Timeline
import nl.mediaguard.app.ui.TimelineEntry
import nl.mediaguard.app.ui.Tone
import nl.mediaguard.app.ui.UiState
import nl.mediaguard.app.ui.refreshing
import nl.mediaguard.app.ui.theme.Mg
import nl.mediaguard.app.ui.theme.MgSpace
import nl.mediaguard.app.ui.theme.cijfers
import nl.mediaguard.app.ui.theme.TouchTarget
import java.time.LocalDate

/** De filterchips van Activiteit (één rij, zoals in de web). */
enum class ActivityTab(val key: String, private val nl: String, private val en: String) {
    Nu("nu", "Nu", "Now"),
    Problemen("problemen", "Problemen", "Problems"),
    Downloads("downloads", "Downloads", "Downloads"),
    Gebeurd("gebeurd", "Wat is er gebeurd", "What happened"),
    Geschiedenis("geschiedenis", "Geschiedenis", "History");

    val label: String get() = tr(nl, en)

    companion object { fun of(key: String?) = entries.firstOrNull { it.key == key } ?: Nu }
}

class ActivityActions(
    val selectTab: (ActivityTab) -> Unit = {},
    val refresh: () -> Unit = {},
    val setDate: (LocalDate) -> Unit = {},
    val pickDate: () -> Unit = {},
    val openWeb: (String) -> Unit = {},
    val retryJob: (Long) -> Unit = {},
    /** Knop uit "Wacht op jou"; het scherm vraagt eerst om bevestiging als dat moet. */
    val action: (nl.mediaguard.app.data.AttentionAction) -> Unit = {},
)

@Composable
fun ActivityContent(
    tab: ActivityTab,
    status: UiState<AppStatus>,
    date: LocalDate,
    day: UiState<DailySummary>,
    history: UiState<List<DailySummary>>,
    a: ActivityActions,
    today: LocalDate = LocalDate.now(),
) {
    val s = (status as? UiState.Data)?.value
    val refreshing = when (tab) {
        ActivityTab.Gebeurd -> day.refreshing
        ActivityTab.Geschiedenis -> history.refreshing
        else -> status.refreshing
    }
    ScreenList(refreshing = refreshing, onRefresh = a.refresh) {
        item { PageHeader(tr("Activiteit", "Activity"), tr("Wat MediaGuard nu doet en wat er gebeurd is", "What MediaGuard is doing now and what happened")) }
        item {
            FilterChips(ActivityTab.entries.map { it to it.label }, tab, a.selectTab,
                counts = mapOf(ActivityTab.Problemen to (s?.attention?.count ?: 0), ActivityTab.Downloads to (s?.downloads?.active ?: 0)))
        }
        when (tab) {
            ActivityTab.Nu, ActivityTab.Problemen, ActivityTab.Downloads -> when (status) {
                UiState.Loading -> item { LoadingState(2) }
                is UiState.Error -> item { ErrorState(status.message, a.refresh) }
                is UiState.Data -> when (tab) {
                    ActivityTab.Nu -> item { NowTab(status.value) }
                    ActivityTab.Problemen -> item { ProblemsTab(status.value, a) }
                    else -> item { DownloadsTab(status.value) }
                }
            }
            ActivityTab.Gebeurd -> {
                item { DateBar(date, today, a) }
                when (day) {
                    UiState.Loading -> item { LoadingState(3) }
                    is UiState.Error -> item { ErrorState(day.message, a.refresh) }
                    is UiState.Data -> happened(day.value, a)
                }
            }
            ActivityTab.Geschiedenis -> when (history) {
                UiState.Loading -> item { LoadingState(3) }
                is UiState.Error -> item { ErrorState(history.message, a.refresh) }
                is UiState.Data -> item { HistoryTab(history.value, a) }
            }
        }
    }
}

// ---------------------------------------------------------------------------------- Nu

/** Kleur van een baan (Peil `domain-lane-gpu/cpu`): alleen voor het icoon en de balk, nooit voor tekst. */
@Composable
fun laneColor(lane: String): Color = when (Baan.of(lane)) {
    Baan.Gpu -> Mg.peil.domainLaneGpu
    Baan.Cpu -> Mg.peil.domainLaneCpu
    Baan.Anders -> Mg.peil.chart1
}

/** Baanlabel: neutraal label met `microchip`/`cpu` in de baankleur, woord in inkt. */
@Composable
fun LaneLabel(lane: String) {
    PeilLabel(Kenmerk(lane.uppercase().ifBlank { "AV1" }, Baan.of(lane).icoon), iconTint = laneColor(lane))
}

@Composable
fun EncodeRow(r: RunningEncode, divider: Boolean) {
    Column(Modifier.fillMaxWidth()) {
        if (divider) Spacer(Modifier.size(MgSpace.s1))
        Row(verticalAlignment = Alignment.CenterVertically) {
            LaneLabel(r.lane)
            Spacer(Modifier.width(MgSpace.s2))
            Text(r.title, style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold), color = Mg.colors.text,
                modifier = Modifier.weight(1f))
            r.percent?.let {
                Spacer(Modifier.width(MgSpace.s2))
                Text("%.0f %%".format(it), style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold).cijfers, color = Mg.colors.text)
            }
        }
        Spacer(Modifier.size(6.dp))
        MgProgress(r.percent?.let { (it / 100).toFloat() }, color = laneColor(r.lane),
            description = r.percent?.let { tr("%.0f procent", "%.0f percent").format(it) })
        Spacer(Modifier.size(4.dp))
        val meta = listOfNotNull(r.phase, formatEta(r.etaSeconds).ifBlank { null }).joinToString(" · ")
        if (meta.isNotBlank()) Text(meta, style = Mg.type.meta, color = Mg.colors.muted)
    }
}

@Composable
fun DownloadRow(d: DownloadItem) {
    Column(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(d.title, style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold), color = Mg.colors.text,
                modifier = Modifier.weight(1f))
            d.percent?.let {
                Spacer(Modifier.width(MgSpace.s2))
                Text("%.0f %%".format(it), style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold).cijfers, color = Mg.colors.text)
            }
        }
        Spacer(Modifier.size(6.dp))
        MgProgress(d.percent?.let { (it / 100).toFloat() }, color = Mg.peil.chart1, description = d.percent?.let { tr("%.0f procent", "%.0f percent").format(it) })
        Spacer(Modifier.size(4.dp))
        Text(listOfNotNull(formatEta(d.etaSeconds).ifBlank { null }, d.client.ifBlank { null }, d.size?.let { formatBytes(it) })
            .joinToString(" · "), style = Mg.type.meta, color = Mg.colors.muted)
    }
}

@Composable
private fun NowTab(s: AppStatus) {
    Column(verticalArrangement = Arrangement.spacedBy(MgSpace.s3)) {
        TileRow {
            KpiTile("Encodes", "${s.encoder.running.size}", Modifier.weight(1f).fillMaxHeight(), sub = tr("${s.encoder.queued} in de wachtrij", "${s.encoder.queued} in the queue"),
                icon = R.drawable.lucide_zap)
            KpiTile("Downloads", "${s.downloads.active}", Modifier.weight(1f).fillMaxHeight(),
                sub = when (s.downloads.clientsOk) {
                    true -> tr("downloadclient bereikbaar", "download client reachable")
                    false -> tr("downloadclient onbereikbaar", "download client unreachable")
                    null -> tr("status onbekend", "status unknown")
                },
                icon = R.drawable.lucide_download, status = PeilStatus.Storing.takeIf { s.downloads.clientsOk == false })
        }
        MgCard {
            CardHeader(tr("AV1-encodes", "AV1 encodes"), R.drawable.lucide_zap, count = s.encoder.running.size)
            if (!s.encoder.reachable) Melding(Tone.Bad, R.drawable.lucide_octagon_x, tr("De AV1-engine is niet bereikbaar.", "The AV1 engine cannot be reached."))
            if (s.encoder.running.isEmpty()) EmptyState(tr("Geen actieve encodes", "No active encodes"), tr("Live banen en wachtrij staan bij Ruimte.", "Live lanes and the queue are under Space."))
            s.encoder.running.forEachIndexed { i, r -> EncodeRow(r, i > 0) }
        }
        MgCard {
            CardHeader("Downloads", R.drawable.lucide_cloud_download, count = s.downloads.active)
            if (s.downloads.items.isEmpty()) EmptyState(tr("Er wordt nu niets gedownload", "Nothing is downloading right now"))
            s.downloads.items.take(5).forEach { DownloadRow(it) }
        }
    }
}

@Composable
private fun ProblemsTab(s: AppStatus, a: ActivityActions) {
    MgCard {
        CardHeader(tr("Problemen en wacht op jou", "Problems and waiting for you"), R.drawable.lucide_list_checks, count = s.attention.count,
            countLabel = "${s.attention.count} open")
        if (s.attention.items.isEmpty())
            EmptyState(tr("Geen problemen", "No problems"), tr("Alles loopt. Gaat er iets mis, dan krijg je een melding.", "All running. If something goes wrong, you get a notification."))
        s.attention.items.forEachIndexed { i, item -> AttentionRow(item, i > 0, a.openWeb, a.retryJob, a.action) }
        if (s.attention.count > s.attention.items.size)
            Text(tr("Nog ${s.attention.count - s.attention.items.size} punten in de web.", "${s.attention.count - s.attention.items.size} more items in the web UI."), style = Mg.type.meta, color = Mg.colors.muted)
    }
}

@Composable
private fun DownloadsTab(s: AppStatus) {
    Column(verticalArrangement = Arrangement.spacedBy(MgSpace.s3)) {
        if (s.downloads.clientsOk == false) Banner(tr("Downloadclient niet bereikbaar", "Download client unreachable"), Tone.Bad, R.drawable.lucide_octagon_x,
            sub = tr("Downloads lopen pas weer als de client bereikbaar is.", "Downloads resume once the client can be reached."))
        MgCard {
            CardHeader("Downloads", R.drawable.lucide_cloud_download, count = s.downloads.active)
            if (s.downloads.items.isEmpty()) EmptyState(tr("Er wordt nu niets gedownload", "Nothing is downloading right now"), tr("Nieuwe afleveringen en upgrades verschijnen hier vanzelf.", "New episodes and upgrades appear here by themselves."))
            s.downloads.items.forEach { DownloadRow(it) }
        }
    }
}

// ---------------------------------------------------------------------------------- Wat is er gebeurd

@Composable
private fun DateBar(date: LocalDate, today: LocalDate, a: ActivityActions) {
    MgCard(padding = androidx.compose.foundation.layout.PaddingValues(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            MgButton("", { a.setDate(date.minusDays(1)) }, kind = ButtonKind.Ghost, icon = R.drawable.lucide_arrow_left,
                contentDescription = tr("Vorige dag", "Previous day"))
            Row(Modifier.weight(1f).heightIn(min = TouchTarget).clickable(onClick = a.pickDate).padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.Center) {
                PeilIcon(R.drawable.lucide_calendar_days, Mg.colors.muted)
                Spacer(Modifier.width(8.dp))
                Text(formatDay(date).replaceFirstChar { it.uppercase() }, style = Mg.type.bodyStrong, color = Mg.colors.text)
            }
            MgButton("", { a.setDate(date.plusDays(1)) }, kind = ButtonKind.Ghost, icon = R.drawable.lucide_arrow_right,
                enabled = date.isBefore(today), contentDescription = tr("Volgende dag", "Next day"))
        }
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.happened(s: DailySummary, a: ActivityActions) {
    item {
        MgCard {
            Text(s.headline.ifBlank { tr("Geen activiteit op deze dag.", "No activity on this day.") }, style = Mg.type.bodyStrong, color = Mg.colors.text)
            Row {
                SummaryStat(tr("Netto", "Net"), formatBytes(s.storage.netBytes, signed = true), Modifier.weight(1f))
                SummaryStat(tr("Erbij", "Added"), formatBytes(s.storage.addedBytes), Modifier.weight(1f))
                SummaryStat(tr("AV1 bespaard", "AV1 saved"), formatBytes(s.storage.savedBytes), Modifier.weight(1f))
            }
            Row {
                SummaryStat(tr("Vervangen", "Replaced"), "${s.encoder.done}", Modifier.weight(1f))
                SummaryStat(tr("Mislukt", "Failed"), "${s.encoder.failed}", Modifier.weight(1f),
                    icon = if (s.encoder.failed > 0) R.drawable.lucide_octagon_x else null)
                SummaryStat(tr("Gedownload", "Downloaded"), "${s.downloaded.size}", Modifier.weight(1f))
            }
        }
    }
    if (s.attention.isNotEmpty()) item {
        MgCard {
            CardHeader(tr("Aandacht", "Attention"), R.drawable.lucide_list_checks, count = s.attention.size)
            s.attention.forEachIndexed { i, x ->
                val kind = AttentionKind.of(x.severity)
                ListRow(x.title, divider = i > 0, leading = { IconBubble(kind.icoon, kind.tone) },
                    trailing = if (!x.href.isNullOrBlank()) ({ MgButton(tr("Bekijken", "View"), { a.openWeb(x.href) }, small = true) }) else null)
            }
        }
    }
    item {
        MgCard {
            CardHeader(tr("Vervangen door AV1", "Replaced by AV1"), R.drawable.lucide_zap, count = s.replaced.size)
            if (s.replaced.isEmpty()) EmptyState(tr("Niets vervangen op deze dag", "Nothing replaced on this day"))
            else Timeline(s.replaced.map { r ->
                TimelineEntry(listOfNotNull(r.title, r.label).joinToString(" · "),
                    "${formatBytes(r.fromSize)} → ${formatBytes(r.toSize)}" + (r.saved?.let { " · −${formatBytes(it)}" } ?: "") +
                        (r.vmaf?.let { " · VMAF %.1f".format(it) } ?: ""),
                    formatClock(r.at))
            })
        }
    }
    item {
        MgCard {
            CardHeader(tr("Gedownload", "Downloaded"), R.drawable.lucide_download, count = s.downloaded.size)
            if (s.downloaded.isEmpty()) EmptyState(tr("Niets gedownload op deze dag", "Nothing downloaded on this day"))
            else Timeline(s.downloaded.map { d ->
                TimelineEntry(listOfNotNull(d.title, d.label).joinToString(" · "),
                    listOfNotNull(if (d.kind == "upgrade") "upgrade" else tr("nieuw", "new"), d.quality, d.source, d.size?.let { formatBytes(it) })
                        .joinToString(" · "),
                    formatClock(d.at))
            })
        }
    }
    if (s.upgrades.isNotEmpty()) item {
        MgCard {
            CardHeader(tr("Upgrades — waarom", "Upgrades — why"), R.drawable.lucide_trending_up, count = s.upgrades.size)
            Timeline(s.upgrades.map { u ->
                TimelineEntry(u.reason ?: "Upgrade", listOfNotNull(u.title, u.label).joinToString(" · "), formatClock(u.at))
            })
        }
    }
    item {
        MgCard {
            CardHeader(tr("Aanvragen", "Requests"), R.drawable.lucide_inbox, count = s.requests.size)
            if (s.requests.isEmpty()) EmptyState(tr("Geen nieuwe aanvragen", "No new requests"))
            s.requests.forEachIndexed { i, r ->
                ListRow(r.title, divider = i > 0,
                    sub = listOfNotNull(if (r.mediaType == "tv") tr("Serie", "Series") else tr("Film", "Movie"), formatTime(r.requestedAt).ifBlank { null })
                        .joinToString(" · "),
                    trailing = r.deliveryState?.let { { PeilLabel(Kenmerk(it, R.drawable.lucide_inbox)) } })
            }
        }
    }
}

@Composable
private fun SummaryStat(label: String, value: String, modifier: Modifier, icon: Int? = null) {
    Column(modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (icon != null) { PeilIcon(icon, Mg.peil.down, size = 18.dp); Spacer(Modifier.width(4.dp)) }
            Text(value, style = Mg.type.bodyStrong, color = Mg.colors.text)
        }
        Text(label, style = Mg.type.meta, color = Mg.colors.muted)
    }
}

// ---------------------------------------------------------------------------------- Geschiedenis

@Composable
private fun HistoryTab(days: List<DailySummary>, a: ActivityActions) {
    MgCard {
        CardHeader(tr("Afgelopen 7 dagen", "Past 7 days"), R.drawable.lucide_calendar_days)
        days.forEachIndexed { i, d ->
            val date = runCatching { LocalDate.parse(d.date) }.getOrNull()
            ListRow(
                date?.let { formatDay(it).replaceFirstChar { c -> c.uppercase() } } ?: d.date,
                divider = i > 0,
                sub = d.headline.ifBlank { tr("Geen activiteit", "No activity") },
                trailingText = if (d.storage.savedBytes > 0) "−${formatBytes(d.storage.savedBytes)}" else null,
                onClick = date?.let { { a.setDate(it); a.selectTab(ActivityTab.Gebeurd) } },
            )
        }
    }
}
