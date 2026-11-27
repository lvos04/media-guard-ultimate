package nl.mediaguard.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import nl.mediaguard.app.data.AppStatus
import nl.mediaguard.app.data.AttentionAction
import nl.mediaguard.app.data.AttentionItem
import nl.mediaguard.app.data.Staleness
import nl.mediaguard.app.data.DailySummary
import nl.mediaguard.app.data.formatBytes
import nl.mediaguard.app.data.formatClockMillis
import nl.mediaguard.app.data.formatDate
import nl.mediaguard.app.data.formatTime
import nl.mediaguard.app.data.today
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.ui.Banner
import nl.mediaguard.app.ui.ButtonKind
import nl.mediaguard.app.ui.CardHeader
import nl.mediaguard.app.ui.EmptyState
import nl.mediaguard.app.ui.IconBubble
import nl.mediaguard.app.ui.KpiTile
import nl.mediaguard.app.ui.LoadingState
import nl.mediaguard.app.ui.MgButton
import nl.mediaguard.app.ui.MgCard
import nl.mediaguard.app.ui.PageHeader
import nl.mediaguard.app.ui.ScreenList
import nl.mediaguard.app.ui.SkeletonLine
import nl.mediaguard.app.ui.StatusBadge
import nl.mediaguard.app.ui.StatusPanel
import nl.mediaguard.app.ui.PeilIcon
import nl.mediaguard.app.ui.summaryStaleSince
import nl.mediaguard.app.ui.PeilStatus
import nl.mediaguard.app.ui.AttentionKind
import nl.mediaguard.app.ui.totalStatus
import nl.mediaguard.app.R
import nl.mediaguard.app.ui.TileRow
import nl.mediaguard.app.ui.Tone
import nl.mediaguard.app.ui.UiState
import nl.mediaguard.app.ui.currentValue
import nl.mediaguard.app.ui.refreshing
import nl.mediaguard.app.ui.theme.Mg

/** Acties vanuit Overzicht naar de rest van de app. */
class OverviewActions(
    val refresh: () -> Unit = {},
    val openAttention: () -> Unit = {},
    val openActivity: () -> Unit = {},
    val openDownloads: () -> Unit = {},
    val openHappened: () -> Unit = {},
    val openLibrary: () -> Unit = {},
    /** Bibliotheek gefilterd op ontbrekend (tegel "Ontbrekend"). */
    val openMissing: () -> Unit = {},
    val openSpace: () -> Unit = {},
    val openSettings: () -> Unit = {},
    val openWeb: (String) -> Unit = {},
    val retryJob: (Long) -> Unit = {},
    /** Knop uit "Wacht op jou"; het scherm vraagt eerst om bevestiging als dat moet. */
    val action: (AttentionAction) -> Unit = {},
)

data class OverviewExtras(
    val phoneWarning: String? = null,
    val update: String? = null,
)

/** De drie deelmetingen komen uit hetzelfde gedateerde mobiele statusrapport. */
internal data class PanelStatus(
    val core: PeilStatus,
    val encoder: PeilStatus,
    val downloads: PeilStatus,
    val total: PeilStatus,
    val staleSince: Long? = null,
) {
    fun badge(measured: PeilStatus): PeilStatus =
        if (staleSince != null && measured != PeilStatus.Onbekend) PeilStatus.Verouderd else measured
}

internal fun panelStatus(status: UiState<AppStatus>, now: Long): PanelStatus {
    if (status !is UiState.Data || status.freshness.lastOkAt == null) {
        return PanelStatus(PeilStatus.Onbekend, PeilStatus.Onbekend, PeilStatus.Onbekend, PeilStatus.Onbekend)
    }
    fun health(ok: Boolean?) = when (ok) {
        true -> PeilStatus.Gezond
        false -> PeilStatus.Storing
        null -> PeilStatus.Onbekend
    }
    val s = status.value
    val staleSince = Staleness.staleSince(status.freshness, now)
    val core = if (s.emergencyStop.active) PeilStatus.Gepauzeerd else PeilStatus.Gezond
    val encoder = health(s.encoder.reachable)
    val downloads = health(s.downloads.clientsOk)
    val attention = totalStatus(s.attention.errors, s.attention.items)
    val total = when {
        staleSince != null -> PeilStatus.Verouderd
        s.emergencyStop.active -> PeilStatus.Gepauzeerd
        encoder == PeilStatus.Storing || downloads == PeilStatus.Storing || attention == PeilStatus.Beperkt -> PeilStatus.Beperkt
        encoder == PeilStatus.Onbekend || downloads == PeilStatus.Onbekend -> PeilStatus.Onbekend
        else -> PeilStatus.Gezond
    }
    return PanelStatus(core, encoder, downloads, total, staleSince)
}

@Composable
fun OverviewContent(
    status: UiState<AppStatus>,
    missing: UiState<Int>,
    recent: UiState<DailySummary>,
    a: OverviewActions,
    extras: OverviewExtras = OverviewExtras(),
    date: java.time.LocalDate = today(),
    now: Long = System.currentTimeMillis(),
) {
    val panel = panelStatus(status, now)
    val report = (status as? UiState.Data)?.takeIf { it.freshness.lastOkAt != null }
    val fresh = status.currentValue(now)
    ScreenList(refreshing = status.refreshing || missing.refreshing || recent.refreshing, onRefresh = a.refresh) {
        item {
            val version = fresh?.version?.ifBlank { null }
            PageHeader(tr("Overzicht", "Overview"), listOfNotNull(formatDate(date), version?.let { "MediaGuard v$it" }).joinToString(" · "))
        }
        extras.phoneWarning?.let { w ->
            item {
                Banner(w, Tone.Info, R.drawable.lucide_bell_off, sub = tr("Zonder meldingen merk je problemen pas als je de app opent.", "Without notifications you only notice problems when you open the app."),
                    action = { MgButton(tr("Oplossen", "Fix"), a.openSettings, small = true) })
            }
        }
        if (status == UiState.Loading) item { LoadingState(3) }
        else item { SystemPanel(status, panel, now, a) }
        if (fresh != null) extras.update?.let { u ->
            item { Banner(u, Tone.Info, R.drawable.lucide_download, action = { MgButton(tr("Bekijken", "View"), a.openSettings, small = true) }) }
        }
        item { Kpis(report?.value, missing, a, panel.staleSince?.let { formatClockMillis(it, now) }, now) }
        item { Recent(recent, a, now) }
        if (fresh != null) item { Waiting(fresh.attention.items, fresh.attention.count, a) }
    }
}

@Composable
private fun SystemPanel(status: UiState<AppStatus>, panel: PanelStatus, now: Long, a: OverviewActions) {
    val data = status as? UiState.Data
    val at = data?.freshness?.lastOkAt
    val time = when {
        at == null -> stringResource(R.string.overview_no_measurement)
        panel.staleSince != null -> stringResource(R.string.overview_not_updated, formatClockMillis(at, now))
        else -> stringResource(R.string.overview_report_time, formatClockMillis(at, now))
    }
    val error = (status as? UiState.Error)?.message ?: data?.freshness?.error
    val attention = data?.value?.attention
    val attentionReason = if (panel.total == PeilStatus.Beperkt && attention != null) {
        when {
            attention.errors > 0 -> stringResource(R.string.overview_attention_errors, attention.errors)
            totalStatus(0, attention.items) == PeilStatus.Beperkt -> stringResource(R.string.overview_attention_warning)
            else -> null
        }
    } else null
    val retry = panel.total == PeilStatus.Verouderd || panel.total == PeilStatus.Onbekend
    StatusPanel(stringResource(R.string.overview_system_status), listOfNotNull(time, error, attentionReason).joinToString("\n"),
        panel.total, actionLabel = if (retry) tr("Opnieuw proberen", "Try again") else null, onAction = a.refresh) {
        val stop = data?.value?.emergencyStop
        val stopText = if (panel.core == PeilStatus.Gepauzeerd && stop != null) {
            listOfNotNull(stringResource(R.string.overview_stop_recorded), stop.since?.let(::formatTime),
                stop.reason?.ifBlank { null }).joinToString(" · ")
        } else null
        SystemRow(stringResource(R.string.overview_core), panel, panel.core, a.openSettings, stopText)
        SystemRow(stringResource(R.string.overview_encoder), panel, panel.encoder, a.openSpace)
        SystemRow(stringResource(R.string.overview_downloads), panel, panel.downloads, a.openDownloads)
        val count = attention?.count ?: 0
        if (at != null && count > 0 && panel.staleSince == null) {
            val label = if (count == 1) tr("1 ding wacht op jou", "1 thing is waiting for you")
                else tr("$count dingen wachten op jou", "$count things are waiting for you")
            MgButton(label, a.openAttention, kind = ButtonKind.Ghost)
        }
    }
}

@Composable
private fun SystemRow(label: String, panel: PanelStatus, measured: PeilStatus, open: () -> Unit, detail: String? = null) {
    val badge = panel.badge(measured)
    Column(Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = open).heightIn(min = 48.dp),
        verticalArrangement = Arrangement.Center) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(label, style = Mg.type.bodyStrong, color = Mg.colors.text, modifier = Modifier.weight(1f))
            StatusBadge(badge)
            PeilIcon(R.drawable.lucide_arrow_right, Mg.colors.muted, size = 18.dp)
        }
        val note = when {
            badge == PeilStatus.Verouderd && measured in listOf(PeilStatus.Storing, PeilStatus.Gepauzeerd) ->
                stringResource(R.string.overview_last_status, detail ?: measured.woord)
            else -> detail
        }
        if (note != null) Text(note, style = Mg.type.meta, color = Mg.colors.muted)
    }
}

@Composable
private fun Kpis(s: AppStatus?, missing: UiState<Int>, a: OverviewActions, stale: String?, now: Long) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        TileRow {
            KpiTile(tr("Bespaard met AV1", "Saved with AV1"), s?.savingsBytes?.let { formatBytes(it) }, Modifier.weight(1f).fillMaxHeight(),
                sub = tr("totaal door AV1", "total through AV1"), icon = R.drawable.lucide_zap, staleSince = stale, onClick = a.openSpace)
            val busy = s?.takeIf { it.encoder.reachable == true && it.downloads.clientsOk == true }
            KpiTile(tr("Nu bezig", "Busy now"), busy?.let { (it.encoder.running.size + it.downloads.active).toString() }, Modifier.weight(1f).fillMaxHeight(),
                sub = busy?.let { "${it.encoder.running.size} encodes · ${it.downloads.active} downloads" },
                icon = R.drawable.lucide_activity, staleSince = stale, onClick = a.openActivity)
        }
        TileRow {
            val count = missing as? UiState.Data
            KpiTile(tr("Ontbrekend", "Missing"), count?.value?.toString(), Modifier.weight(1f).fillMaxHeight(),
                sub = stringResource(R.string.overview_filtered_library), icon = R.drawable.lucide_search_x,
                staleSince = count?.summaryStaleSince(now)?.let { formatClockMillis(it, now) },
                onClick = if ((count?.value ?: 0) > 0) a.openMissing else a.openLibrary)
            KpiTile(tr("Vrije ruimte", "Free space"), s?.storage?.freeBytes?.let { formatBytes(it) }, Modifier.weight(1f).fillMaxHeight(),
                sub = s?.storage?.daysLeft?.let { tr("vol over $it", "full in $it") },
                icon = R.drawable.lucide_hard_drive, staleSince = stale, onClick = a.openSpace)
        }
    }
}

/**
 * Titel van de kaart: rollend venster of (oudere server) het overzicht van gisteren.
 *
 * Het venster "24h" is een bekende code. In het Nederlands toont de app de tekst van de server; in het Engels kiest
 * de app zelf de titel, net als bij een bekende Seerr-status (ADR-0023 punt 8). Zo staat er geen Nederlandse
 * kaarttitel tussen Engelse app-teksten.
 */
fun recentTitle(d: DailySummary?): String = when {
    d == null -> tr("Laatste 24 uur", "Last 24 hours")
    d.window == "24h" -> tr(d.label ?: "Laatste 24 uur", "Last 24 hours")
    else -> tr("Gisteren", "Yesterday")
}

@Composable
private fun Recent(recent: UiState<DailySummary>, a: OverviewActions, now: Long) {
    MgCard {
        CardHeader(recentTitle((recent as? UiState.Data)?.value), R.drawable.lucide_history, trailing = {
            MgButton("Details", a.openHappened, kind = ButtonKind.Ghost, small = true)
        })
        SummaryReading(recent, now) { d ->
            Text(d.headline.ifBlank { tr("Geen activiteit.", "No activity.") }, style = Mg.type.body, color = Mg.colors.text)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Stat(tr("Netto", "Net"), formatBytes(d.storage.netBytes, signed = true), Modifier.weight(1f))
                Stat(tr("AV1 bespaard", "AV1 saved"), formatBytes(d.storage.savedBytes.takeIf { it > 0 } ?: d.encoder.savedBytes), Modifier.weight(1f))
                Stat(tr("Erbij", "Added"), "${d.downloaded.size}", Modifier.weight(1f))
            }
        }
    }
}

/** Dezelfde lezing voedt de kaart op Overzicht en de opslaggrafiek op Ruimte. */
@Composable
internal fun SummaryReading(recent: UiState<DailySummary>, now: Long, content: @Composable (DailySummary) -> Unit) {
    when (recent) {
        UiState.Loading -> SkeletonLine(0.8f)
        is UiState.Error -> Text(recent.message, style = Mg.type.small, color = Mg.colors.muted)
        is UiState.Data -> {
            if (recent.freshness.lastOkAt == null) {
                StatusBadge(PeilStatus.Onbekend, reason = stringResource(R.string.overview_no_measurement))
            } else {
                recent.summaryStaleSince(now)?.let {
                    StatusBadge(PeilStatus.Verouderd, reason = stringResource(R.string.overview_last_status, formatClockMillis(it, now)))
                }
                content(recent.value)
            }
        }
    }
}

@Composable
private fun Stat(label: String, value: String, modifier: Modifier) {
    Column(modifier) {
        Text(value, style = Mg.type.bodyStrong, color = Mg.colors.text)
        Text(label, style = Mg.type.meta, color = Mg.colors.muted)
    }
}

@Composable
private fun Waiting(items: List<AttentionItem>, count: Int, a: OverviewActions) {
    MgCard {
        CardHeader(tr("Wat actie nodig heeft", "What needs action"), R.drawable.lucide_list_checks, count = count,
            countLabel = if (count == 1) tr("1 ding wacht op jou", "1 thing is waiting for you") else tr("$count dingen wachten op jou", "$count things are waiting for you"))
        if (items.isEmpty()) EmptyState(tr("Niets wacht op jou", "Nothing is waiting for you"), tr("Komt er iets bij, dan zie je het hier en krijg je een melding.", "When something comes up, you see it here and get a notification."))
        items.take(6).forEachIndexed { i, item -> AttentionRow(item, i > 0, a.openWeb, a.retryJob, a.action, compact = true) }
        if (items.size > 6 || count > items.size) MgButton(tr("Alle $count bekijken", "View all $count"), a.openAttention, kind = ButtonKind.Ghost, small = true)
    }
}

/**
 * Eén punt uit "Wacht op jou": ernst, titel, de uitleg van MediaGuard en dezelfde knoppen als in de web
 * (zelfde endpoints). Met [compact] (Overzicht) zonder de regels per bestand.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AttentionRow(
    item: AttentionItem,
    divider: Boolean,
    openWeb: (String) -> Unit,
    retryJob: (Long) -> Unit,
    action: (AttentionAction) -> Unit = {},
    compact: Boolean = false,
) {
    val kind = AttentionKind.of(item)
    val jobId = item.jobId
    Column(Modifier.fillMaxWidth()) {
        if (divider) Box(Modifier.fillMaxWidth().height(1.dp).background(Mg.colors.lineSoft))
        Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.Top) {
            Box(Modifier.semantics { contentDescription = kind.woord }) { IconBubble(kind.icoon, kind.tone) }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(item.title, style = Mg.type.body.copy(fontWeight = FontWeight.SemiBold), color = Mg.colors.text)
                item.detail?.ifBlank { null }?.let { Text(it, style = Mg.type.small, color = Mg.colors.muted) }
                if (kind == AttentionKind.Voorwaarde) Text(tr("Dit is geen storing; er wacht een voorwaarde.", "This is not a fault; a condition is waiting."),
                    style = Mg.type.small, color = Mg.colors.muted)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    item.actions.forEach { act ->
                        MgButton(act.label, { action(act) }, kind = if (act.primary) ButtonKind.Primary else ButtonKind.Secondary, small = true)
                    }
                    if (jobId != null) MgButton(tr("Opnieuw proberen", "Try again"), { retryJob(jobId) }, small = true)
                    if (!item.href.isNullOrBlank()) MgButton(tr("Bekijken", "View"), { openWeb(item.href) },
                        kind = if (item.actions.isEmpty()) ButtonKind.Primary else ButtonKind.Ghost, small = true)
                }
                if (!compact) item.rows.forEach { r ->
                    Column(Modifier.fillMaxWidth().padding(top = 4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(r.title, style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold), color = Mg.colors.text)
                        r.detail?.ifBlank { null }?.let { Text(it, style = Mg.type.meta, color = Mg.colors.muted) }
                        if (r.actions.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            r.actions.forEach { act ->
                                MgButton(act.label, { action(act) }, kind = if (act.primary) ButtonKind.Primary else ButtonKind.Secondary, small = true)
                            }
                        }
                    }
                }
                val more = buildList {
                    if (compact && item.rows.isNotEmpty()) add(tr("Per bestand kiezen: Activiteit → Problemen", "Choose per file: Activity → Problems"))
                    else if (item.moreRows > 0) add(tr("Nog ${item.moreRows} in de web", "${item.moreRows} more in the web UI"))
                    if (item.adminActions > 0) add(tr("Meer knoppen in de web (alleen beheerder)", "More buttons in the web UI (admin only)"))
                }
                if (more.isNotEmpty()) Text(more.joinToString(" · "), style = Mg.type.meta, color = Mg.colors.muted)
            }
        }
    }
}
