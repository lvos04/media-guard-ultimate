package nl.mediaguard.app.ui.screens

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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import nl.mediaguard.app.data.AppStatus
import nl.mediaguard.app.data.AttentionAction
import nl.mediaguard.app.data.AttentionItem
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
import nl.mediaguard.app.ui.ErrorState
import nl.mediaguard.app.ui.IconBubble
import nl.mediaguard.app.ui.KpiTile
import nl.mediaguard.app.ui.LoadingState
import nl.mediaguard.app.ui.MgButton
import nl.mediaguard.app.ui.MgCard
import nl.mediaguard.app.ui.PageHeader
import nl.mediaguard.app.ui.ScreenList
import nl.mediaguard.app.ui.StatusPanel
import nl.mediaguard.app.ui.PeilStatus
import nl.mediaguard.app.ui.AttentionKind
import nl.mediaguard.app.ui.totalStatus
import nl.mediaguard.app.R
import nl.mediaguard.app.ui.TileRow
import nl.mediaguard.app.ui.Tone
import nl.mediaguard.app.ui.UiState
import nl.mediaguard.app.ui.refreshing
import nl.mediaguard.app.ui.theme.Mg

/** Acties vanuit Overzicht naar de rest van de app. */
class OverviewActions(
    val refresh: () -> Unit = {},
    val openAttention: () -> Unit = {},
    val openActivity: () -> Unit = {},
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

/** Extra toestand boven de status: verouderd, meldingen op de telefoon, updates. */
data class OverviewExtras(
    /** Tijdstip (epoch ms) van de laatst geslaagde status als die verouderd is, anders null. */
    val staleSince: Long? = null,
    val staleError: String? = null,
    val phoneWarning: String? = null,
    val update: String? = null,
)

/**
 * Het statuspaneel bovenaan (Peil SysteemStatusPaneel): een noodstop of verouderde gegevens gaan vóór de lijst.
 * [status] is de totaalstatus in Peil-vorm: noodstop = Gepauzeerd (bewuste stop, ADR-0016 punt 9), verouderd =
 * Verouderd, anders Beperkt bij een storing of waarschuwing in de lijst en Gezond als er niets misgaat.
 */
data class HeroModel(val title: String, val sub: String?, val status: PeilStatus, val kind: Kind, val statusLabel: String? = null) {
    enum class Kind { Stop, Stale, Waiting, Ok }
}

fun heroFor(s: AppStatus, staleSince: Long?): HeroModel {
    val n = s.attention.count
    return when {
        s.emergencyStop.active -> HeroModel(
            tr("Noodstop actief", "Emergency stop active"),
            listOfNotNull(tr("Nieuwe grabs, encodes en vervangingen staan stil.", "New grabs, encodes and replacements are on hold."),
                s.emergencyStop.since?.let { tr("Sinds ${formatTime(it)}.", "Since ${formatTime(it)}.") }, s.emergencyStop.reason?.ifBlank { null })
                .joinToString(" "),
            PeilStatus.Gepauzeerd, HeroModel.Kind.Stop)
        staleSince != null -> HeroModel(
            tr("Niet bijgewerkt sinds ${formatClockMillis(staleSince)}", "Not updated since ${formatClockMillis(staleSince)}"),
            tr("MediaGuard antwoordt niet. Je ziet de laatst bekende stand.", "MediaGuard is not responding. You see the last known state."),
            PeilStatus.Verouderd, HeroModel.Kind.Stale)
        n > 0 -> {
            val total = totalStatus(s.attention.errors, s.attention.items)
            HeroModel(
                if (n == 1) tr("1 ding wacht op jou", "1 thing is waiting for you") else tr("$n dingen wachten op jou", "$n things are waiting for you"),
                if (s.attention.errors == 0) tr("Er loopt nu niets vast; MediaGuard houdt alles in de gaten.", "Nothing is stuck right now; MediaGuard keeps watch.")
                else tr("${s.attention.errors} met een fout. De rest gaat gewoon door.", "${s.attention.errors} with an error. The rest carries on."),
                total, HeroModel.Kind.Waiting, statusLabel = if (total == PeilStatus.Gezond) tr("Alles gezond", "All healthy") else null)
        }
        else -> HeroModel(tr("Alles loopt — niets nodig van jou", "All running — nothing needed from you"),
            tr("Niets wacht op jou. MediaGuard zoekt, downloadt en bespaart vanzelf verder.", "Nothing is waiting for you. MediaGuard keeps searching, downloading and saving space by itself."), PeilStatus.Gezond,
            HeroModel.Kind.Ok, statusLabel = tr("Alles gezond", "All healthy"))
    }
}

@Composable
fun OverviewContent(
    status: UiState<AppStatus>,
    missing: Int?,
    recent: UiState<DailySummary>,
    a: OverviewActions,
    extras: OverviewExtras = OverviewExtras(),
    /** Datum in de paginakop (vast in de schermafdrukken). */
    date: java.time.LocalDate = today(),
) {
    ScreenList(refreshing = status.refreshing, onRefresh = a.refresh) {
        item {
            val version = (status as? UiState.Data)?.value?.version?.ifBlank { null }
            PageHeader(tr("Overzicht", "Overview"), listOfNotNull(formatDate(date), version?.let { "MediaGuard v$it" }).joinToString(" · "))
        }
        extras.phoneWarning?.let { w ->
            item {
                Banner(w, Tone.Info, R.drawable.lucide_bell_off, sub = tr("Zonder meldingen merk je problemen pas als je de app opent.", "Without notifications you only notice problems when you open the app."),
                    action = { MgButton(tr("Oplossen", "Fix"), a.openSettings, small = true) })
            }
        }
        when (status) {
            UiState.Loading -> item { LoadingState(3) }
            is UiState.Error -> item { ErrorState(status.message, a.refresh) }
            is UiState.Data -> {
                val s = status.value
                item { Hero(heroFor(s, extras.staleSince), extras.staleError, a) }
                extras.update?.let { u ->
                    item { Banner(u, Tone.Info, R.drawable.lucide_download, action = { MgButton(tr("Bekijken", "View"), a.openSettings, small = true) }) }
                }
                item { Kpis(s, missing, a, extras.staleSince?.let { formatClockMillis(it) }) }
                item { Recent(recent, a) }
                item { Waiting(s.attention.items, s.attention.count, a) }
            }
        }
    }
}

@Composable
private fun Hero(h: HeroModel, staleError: String?, a: OverviewActions) {
    val icon = when (h.kind) {
        HeroModel.Kind.Waiting -> R.drawable.lucide_list_checks
        else -> h.status.icoon
    }
    val sub = if (h.kind == HeroModel.Kind.Stale && staleError != null) "${h.sub} $staleError" else h.sub
    val (label, onAction) = when (h.kind) {
        HeroModel.Kind.Stop -> tr("Naar Ruimte", "Go to Space") to a.openSpace
        HeroModel.Kind.Stale -> tr("Opnieuw proberen", "Try again") to a.refresh
        HeroModel.Kind.Waiting -> tr("Naar de lijst", "Go to the list") to a.openAttention
        HeroModel.Kind.Ok -> null to null
    }
    StatusPanel(h.title, sub, h.status, statusLabel = h.statusLabel, icon = icon, actionLabel = label, onAction = onAction)
}

/** KPI-tegels; met [stale] (laatst bekende tijd) zijn ze gestippeld met "Laatst bekend", zoals het statuspaneel. */
@Composable
private fun Kpis(s: AppStatus, missing: Int?, a: OverviewActions, stale: String?) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        TileRow {
            KpiTile(tr("Bespaard met AV1", "Saved with AV1"), if (s.savingsBytes > 0) formatBytes(s.savingsBytes) else "—", Modifier.weight(1f).fillMaxHeight(),
                sub = tr("kleiner, zelfde kwaliteit", "smaller, same quality"), icon = R.drawable.lucide_zap, staleSince = stale, onClick = a.openSpace)
            val busy = s.encoder.running.size + s.downloads.active
            KpiTile(tr("Nu bezig", "Busy now"), "$busy", Modifier.weight(1f).fillMaxHeight(),
                sub = "${s.encoder.running.size} encodes · ${s.downloads.active} downloads",
                icon = R.drawable.lucide_activity, staleSince = stale, onClick = a.openActivity)
        }
        TileRow {
            KpiTile(tr("Ontbrekend", "Missing"), missing?.toString(), Modifier.weight(1f).fillMaxHeight(),
                sub = if (missing == null) tr("nog niet bekend", "not known yet") else tr("MediaGuard zoekt vanzelf", "MediaGuard searches by itself"),
                icon = R.drawable.lucide_search_x, staleSince = stale.takeIf { missing != null },
                onClick = if ((missing ?: 0) > 0) a.openMissing else a.openLibrary)
            KpiTile(tr("Vrije ruimte", "Free space"), formatBytes(s.storage.freeBytes), Modifier.weight(1f).fillMaxHeight(),
                sub = s.storage.daysLeft?.let { tr("vol over $it", "full in $it") } ?: tr("nog geen prognose", "no forecast yet"),
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
private fun Recent(recent: UiState<DailySummary>, a: OverviewActions) {
    MgCard {
        CardHeader(recentTitle((recent as? UiState.Data)?.value), R.drawable.lucide_history, trailing = {
            MgButton("Details", a.openHappened, kind = ButtonKind.Ghost, small = true)
        })
        when (recent) {
            UiState.Loading -> nl.mediaguard.app.ui.SkeletonLine(0.8f)
            is UiState.Error -> Text(recent.message, style = Mg.type.small, color = Mg.colors.muted)
            is UiState.Data -> {
                val d = recent.value
                Text(d.headline.ifBlank { tr("Geen activiteit.", "No activity.") }, style = Mg.type.body, color = Mg.colors.text)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Stat(tr("Netto", "Net"), formatBytes(d.storage.netBytes, signed = true), Modifier.weight(1f))
                    Stat(tr("AV1 bespaard", "AV1 saved"), formatBytes(d.storage.savedBytes.takeIf { it > 0 } ?: d.encoder.savedBytes), Modifier.weight(1f))
                    Stat(tr("Erbij", "Added"), "${d.downloaded.size}", Modifier.weight(1f))
                }
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
    val jobId = item.href?.let { Regex("job=(\\d+)").find(it)?.groupValues?.get(1)?.toLongOrNull() }
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
