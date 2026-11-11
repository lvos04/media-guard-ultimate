package nl.mediaguard.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
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
import nl.mediaguard.app.data.Lane
import nl.mediaguard.app.data.RunningEncode
import nl.mediaguard.app.data.formatDuur
import nl.mediaguard.app.data.formatBytes
import nl.mediaguard.app.data.formatDay
import nl.mediaguard.app.data.formatTime
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.ui.ButtonKind
import nl.mediaguard.app.ui.CardHeader
import nl.mediaguard.app.ui.EmptyState
import nl.mediaguard.app.ui.ErrorState
import nl.mediaguard.app.ui.KpiTile
import nl.mediaguard.app.ui.LoadingState
import nl.mediaguard.app.ui.MgButton
import nl.mediaguard.app.ui.MgCard
import nl.mediaguard.app.ui.MgProgress
import nl.mediaguard.app.ui.PageHeader
import nl.mediaguard.app.ui.PeilLabel
import nl.mediaguard.app.ui.PeilIcon
import nl.mediaguard.app.ui.Kenmerk
import nl.mediaguard.app.ui.StatusBadge
import nl.mediaguard.app.ui.PeilStatus
import nl.mediaguard.app.ui.Melding
import nl.mediaguard.app.ui.arcering
import nl.mediaguard.app.ui.theme.cijfers
import nl.mediaguard.app.R
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.ui.graphics.Color
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.padding
import nl.mediaguard.app.ui.ScreenList
import nl.mediaguard.app.ui.SkeletonLine
import nl.mediaguard.app.ui.TileRow
import nl.mediaguard.app.ui.Tone
import nl.mediaguard.app.ui.UiState
import nl.mediaguard.app.ui.refreshing
import nl.mediaguard.app.ui.theme.Mg
import nl.mediaguard.app.ui.theme.MgSpace
import java.time.LocalDate
import kotlin.math.abs

class SpaceActions(
    val refresh: () -> Unit = {},
    /** true = aanzetten, false = opheffen; het scherm vraagt eerst om bevestiging. */
    val emergency: (Boolean) -> Unit = {},
    /** (baan, nu gepauzeerd?) */
    val lane: (String, Boolean) -> Unit = { _, _ -> },
    val autopilot: () -> Unit = {},
    val openHappened: () -> Unit = {},
)

@Composable
fun SpaceContent(status: UiState<AppStatus>, recent: UiState<DailySummary>, a: SpaceActions) {
    ScreenList(refreshing = status.refreshing, onRefresh = a.refresh) {
        item { PageHeader(tr("Ruimte", "Space"), tr("Ruimte besparen zonder kwaliteitsverlies: elke encode wordt gecontroleerd voordat hij het origineel vervangt.", "Save space without quality loss: every encode is checked before it replaces the original.")) }
        when (status) {
            UiState.Loading -> item { LoadingState(3) }
            is UiState.Error -> item { ErrorState(status.message, a.refresh) }
            is UiState.Data -> {
                val s = status.value
                item { EmergencyCard(s, a) }
                item { SpaceKpis(s) }
                item { EncodesNow(s, a) }
                item { Queue(s, a) }
            }
        }
        item { WhereDidSpaceGo(recent, a) }
    }
}

/**
 * Noodstopkaart: aan = Gepauzeerd-vorm "Noodstop actief" (een bewuste stop, geen gemeten falen; ADR-0016 punt 9),
 * uit = neutraal label "Uit" (nooit groen). De knop om hem aan te zetten is een gevaarknop van 48 dp.
 */
@Composable
private fun EmergencyCard(s: AppStatus, a: SpaceActions) {
    val on = s.emergencyStop.active
    MgCard(tone = if (on) Tone.Paused else null) {
        CardHeader(tr("Noodstop", "Emergency stop"), if (on) R.drawable.lucide_circle_pause else R.drawable.lucide_octagon_alert)
        if (on) StatusBadge(PeilStatus.Gepauzeerd, label = tr("Noodstop actief", "Emergency stop active"))
        else PeilLabel(Kenmerk(tr("Uit", "Off"), R.drawable.lucide_power_off))
        Text(
            if (on) listOfNotNull(tr("Nieuwe grabs, encodes en vervangingen staan stil.", "New grabs, encodes and replacements are on hold."), s.emergencyStop.reason?.ifBlank { null },
                s.emergencyStop.since?.let { tr("Sinds ${formatTime(it)}.", "Since ${formatTime(it)}.") }).joinToString(" ")
            else tr("Stopt in één keer nieuwe grabs, encodes en vervangingen; lopende encodes pauzeren.", "Stops new grabs, encodes and replacements at once; running encodes pause."),
            style = Mg.type.small, color = Mg.colors.muted,
        )
        if (on) MgButton(tr("Noodstop opheffen", "Release emergency stop"), { a.emergency(false) }, kind = ButtonKind.Primary, modifier = Modifier.fillMaxWidth())
        else MgButton(tr("Noodstop aanzetten", "Turn on emergency stop"), { a.emergency(true) }, kind = ButtonKind.Danger,
            icon = R.drawable.lucide_octagon_alert, modifier = Modifier.fillMaxWidth())
    }
}

@Composable
private fun SpaceKpis(s: AppStatus) {
    Column(verticalArrangement = Arrangement.spacedBy(MgSpace.s3)) {
        TileRow {
            KpiTile(tr("Bespaard", "Saved"), if (s.savingsBytes > 0) formatBytes(s.savingsBytes) else "—", Modifier.weight(1f).fillMaxHeight(),
                sub = tr("totaal door AV1", "total through AV1"), icon = R.drawable.lucide_zap)
            KpiTile(tr("Vrije ruimte", "Free space"), formatBytes(s.storage.freeBytes), Modifier.weight(1f).fillMaxHeight(),
                sub = s.storage.daysLeft?.let { tr("vol over $it", "full in $it") }, icon = R.drawable.lucide_hard_drive)
        }
        TileRow {
            KpiTile(tr("Wachtrij", "Queue"), "${s.encoder.queued}", Modifier.weight(1f).fillMaxHeight(), sub = tr("jobs wachten", "jobs waiting"),
                icon = R.drawable.lucide_list_ordered)
            val slots = s.encoder.lanes.sumOf { it.slots }
            KpiTile(tr("Bezig", "Busy"), "${s.encoder.running.size}", Modifier.weight(1f).fillMaxHeight(),
                sub = if (slots > 0) tr("van $slots plekken", "of $slots slots") else null, icon = R.drawable.lucide_activity)
        }
    }
}

@Composable
private fun EncodesNow(s: AppStatus, a: SpaceActions) {
    MgCard {
        CardHeader(tr("Encodes nu", "Encodes now"), R.drawable.lucide_zap, count = s.encoder.running.size)
        if (!s.encoder.reachable) Melding(Tone.Bad, R.drawable.lucide_octagon_x,
            tr("De AV1-engine reageert niet.", "The AV1 engine is not responding."), tr("MediaGuard probeert het automatisch opnieuw.", "MediaGuard retries automatically."))
        s.encoder.lanes.forEach { l -> LaneRow(l, s.encoder.running.filter { it.lane == l.lane }, a) }
        if (s.encoder.running.isEmpty()) EmptyState(tr("Geen actieve encodes", "No active encodes"), tr("Zodra er werk is, start de volgende vanzelf.", "As soon as there is work, the next one starts by itself."))
        s.encoder.running.forEachIndexed { i, r -> EncodeRow(r, i > 0) }
    }
}

/** "2 van 4 bezig" (of "uit" als de baan geen plekken heeft), met de eerstvolgende ETA op deze baan. */
fun laneSummary(l: Lane, running: List<RunningEncode>): Pair<String, String?> {
    val head = when {
        l.slots == 0 && l.running == 0 -> tr("uit", "off")
        else -> tr("${l.running} van ${l.slots} bezig", "${l.running} of ${l.slots} busy")
    } + if (l.paused) tr(" · gepauzeerd", " · paused") else ""
    val eta = running.mapNotNull { it.etaSeconds }.filter { it > 0 }.minOrNull()
    return head to eta?.let { tr("eerstvolgende klaar over ${formatDuur(it)}", "next one done in ${formatDuur(it)}") }
}

@Composable
private fun LaneRow(l: Lane, running: List<RunningEncode>, a: SpaceActions) {
    val (head, eta) = laneSummary(l, running)
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            LaneLabel(l.lane)
            Spacer(Modifier.width(MgSpace.s2))
            Column(Modifier.weight(1f)) {
                Text(head, style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold), color = Mg.colors.text)
                eta?.let { Text(it, style = Mg.type.meta, color = Mg.colors.muted) }
            }
            MgButton(if (l.paused) tr("Hervatten", "Resume") else tr("Pauzeren", "Pause"), { a.lane(l.lane, l.paused) }, small = true,
                icon = if (l.paused) R.drawable.lucide_play else R.drawable.lucide_pause)
        }
        if (l.paused) StatusBadge(PeilStatus.Gepauzeerd)
        MgProgress(if (l.slots > 0) l.running.toFloat() / l.slots else 0f, color = laneColor(l.lane), height = 6.dp,
            description = tr("${l.running} van ${l.slots} plekken bezig", "${l.running} of ${l.slots} slots busy"))
        l.reason?.let {
            Row(verticalAlignment = Alignment.Top) {
                PeilIcon(R.drawable.lucide_info, Mg.peil.info, Modifier.padding(top = 1.dp), size = 18.dp)
                Spacer(Modifier.width(6.dp))
                Text(it, style = Mg.type.meta, color = Mg.colors.text)
            }
        }
    }
}

@Composable
private fun Queue(s: AppStatus, a: SpaceActions) {
    MgCard {
        CardHeader(tr("Wachtrij", "Queue"), R.drawable.lucide_list_ordered, count = s.encoder.queued, trailing = {
            MgButton(tr("Autopilot nu", "Autopilot now"), a.autopilot, small = true)
        })
        Text(if (s.encoder.queued == 0) tr("Er wacht niets. De autopilot zet nieuw werk klaar zodra er iets te besparen valt.", "Nothing is waiting. The autopilot queues new work as soon as there is something to save.")
            else tr("${s.encoder.queued} jobs wachten op een vrije plek.", "${s.encoder.queued} jobs are waiting for a free slot."), style = Mg.type.small, color = Mg.colors.muted)
    }
}

/**
 * "Waar ging de ruimte heen": grafiekslots (nooit statuskleuren): Erbij `chart-1`, AV1-besparing `chart-2`,
 * Niet verklaard `chart-other` met arcering (PEIL_MIGRATIE §10.3). Nog niet gemeten = Onbekend, geen balk van 0.
 */
@Composable
private fun WhereDidSpaceGo(recent: UiState<DailySummary>, a: SpaceActions) {
    MgCard {
        CardHeader(tr("Waar ging de ruimte heen", "Where the space went"), R.drawable.lucide_scale, trailing = {
            MgButton("Details", a.openHappened, kind = ButtonKind.Ghost, small = true)
        })
        when (recent) {
            UiState.Loading -> SkeletonLine(0.7f)
            is UiState.Error -> Text(recent.message, style = Mg.type.small, color = Mg.colors.muted)
            is UiState.Data -> {
                val st = recent.value.storage
                val date = runCatching { LocalDate.parse(recent.value.date) }.getOrNull()
                val label = if (recent.value.window != null) recentTitle(recent.value)
                else date?.let { formatDay(it).replaceFirstChar { c -> c.uppercase() } } ?: tr("Gisteren", "Yesterday")
                Text(tr("$label: netto ${formatBytes(st.netBytes, signed = true)}", "$label: net ${formatBytes(st.netBytes, signed = true)}"), style = Mg.type.bodyStrong, color = Mg.colors.text)
                val max = listOf(st.addedBytes, st.savedBytes, st.unexplainedBytes ?: 0).maxOf { abs(it) }.coerceAtLeast(1)
                SpaceBar(tr("Erbij", "Added"), tr("nieuwe bestanden en upgrades", "new files and upgrades"), st.addedBytes, max, Mg.peil.chart1, plus = true)
                SpaceBar(tr("AV1-besparing", "AV1 savings"), tr("bestanden kleiner gemaakt", "files made smaller"), st.savedBytes, max, Mg.peil.chart2, plus = false)
                val unexplained = st.unexplainedBytes
                if (unexplained == null) SpaceBar(tr("Niet verklaard", "Unexplained"), tr("nog niet gemeten (de vrije ruimte wordt elk uur gemeten)", "not measured yet (free space is measured every hour)"),
                    null, max, Mg.peil.chartOther, plus = true, hatched = true)
                else SpaceBar(tr("Niet verklaard", "Unexplained"), tr("verschil dat MediaGuard niet kan toewijzen (bv. andere apps op de pool)", "difference MediaGuard cannot attribute (e.g. other apps on the pool)"),
                    unexplained, max, Mg.peil.chartOther, plus = unexplained >= 0, hatched = true)
            }
        }
    }
}

@Composable
private fun SpaceBar(label: String, sub: String, bytes: Long?, max: Long, color: Color, plus: Boolean, hatched: Boolean = false) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(12.dp).clip(RoundedCornerShape(2.dp)).background(color)
                .then(if (hatched) Modifier.arcering(Mg.peil.card, spacing = 4.dp, width = 1.dp) else Modifier))
            Spacer(Modifier.width(8.dp))
            Text(label, style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold), color = Mg.colors.text, modifier = Modifier.weight(1f))
            if (bytes == null) StatusBadge(PeilStatus.Onbekend)
            else Text((if (bytes == 0L) "" else if (plus) "+" else "−") + formatBytes(abs(bytes)),
                style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold).cijfers, color = Mg.colors.text)
        }
        if (bytes != null) {
            val f = abs(bytes).toFloat() / max
            Box(Modifier.fillMaxWidth().height(10.dp).clip(RoundedCornerShape(4.dp)).background(Mg.peil.card)
                .border(1.dp, Mg.peil.borderStrong, RoundedCornerShape(4.dp))) {
                Box(Modifier.fillMaxHeight().fillMaxWidth(f.coerceIn(0f, 1f)).clip(RoundedCornerShape(4.dp)).background(color)
                    .then(if (hatched) Modifier.arcering(Mg.peil.card, spacing = 5.dp, width = 1.5.dp) else Modifier))
            }
        }
        Text(sub, style = Mg.type.meta, color = Mg.colors.muted)
    }
}
