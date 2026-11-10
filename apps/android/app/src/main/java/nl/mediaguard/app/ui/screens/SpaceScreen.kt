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
import nl.mediaguard.app.data.formatEta
import nl.mediaguard.app.data.formatBytes
import nl.mediaguard.app.data.formatDay
import nl.mediaguard.app.data.formatTime
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
        item { PageHeader("Ruimte", "Ruimte besparen zonder kwaliteitsverlies: elke encode wordt gecontroleerd voordat hij het origineel vervangt.") }
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
        CardHeader("Noodstop", if (on) R.drawable.lucide_circle_pause else R.drawable.lucide_octagon_alert)
        if (on) StatusBadge(PeilStatus.Gepauzeerd, label = "Noodstop actief")
        else PeilLabel(Kenmerk("Uit", R.drawable.lucide_power_off))
        Text(
            if (on) listOfNotNull("Nieuwe grabs, encodes en vervangingen staan stil.", s.emergencyStop.reason?.ifBlank { null },
                s.emergencyStop.since?.let { "Sinds ${formatTime(it)}." }).joinToString(" ")
            else "Stopt in één keer nieuwe grabs, encodes en vervangingen; lopende encodes pauzeren.",
            style = Mg.type.small, color = Mg.colors.muted,
        )
        if (on) MgButton("Noodstop opheffen", { a.emergency(false) }, kind = ButtonKind.Primary, modifier = Modifier.fillMaxWidth())
        else MgButton("Noodstop aanzetten", { a.emergency(true) }, kind = ButtonKind.Danger,
            icon = R.drawable.lucide_octagon_alert, modifier = Modifier.fillMaxWidth())
    }
}

@Composable
private fun SpaceKpis(s: AppStatus) {
    Column(verticalArrangement = Arrangement.spacedBy(MgSpace.s3)) {
        TileRow {
            KpiTile("Bespaard", if (s.savingsBytes > 0) formatBytes(s.savingsBytes) else "—", Modifier.weight(1f).fillMaxHeight(),
                sub = "totaal door AV1", icon = R.drawable.lucide_zap)
            KpiTile("Vrije ruimte", formatBytes(s.storage.freeBytes), Modifier.weight(1f).fillMaxHeight(),
                sub = s.storage.daysLeft?.let { "vol over $it" }, icon = R.drawable.lucide_hard_drive)
        }
        TileRow {
            KpiTile("Wachtrij", "${s.encoder.queued}", Modifier.weight(1f).fillMaxHeight(), sub = "jobs wachten",
                icon = R.drawable.lucide_list_ordered)
            val slots = s.encoder.lanes.sumOf { it.slots }
            KpiTile("Bezig", "${s.encoder.running.size}", Modifier.weight(1f).fillMaxHeight(),
                sub = if (slots > 0) "van $slots plekken" else null, icon = R.drawable.lucide_activity)
        }
    }
}

@Composable
private fun EncodesNow(s: AppStatus, a: SpaceActions) {
    MgCard {
        CardHeader("Encodes nu", R.drawable.lucide_zap, count = s.encoder.running.size)
        if (!s.encoder.reachable) Melding(Tone.Bad, R.drawable.lucide_octagon_x,
            "De AV1-engine reageert niet.", "MediaGuard probeert het automatisch opnieuw.")
        s.encoder.lanes.forEach { l -> LaneRow(l, s.encoder.running.filter { it.lane == l.lane }, a) }
        if (s.encoder.running.isEmpty()) EmptyState("Geen actieve encodes", "Zodra er werk is, start de volgende vanzelf.")
        s.encoder.running.forEachIndexed { i, r -> EncodeRow(r, i > 0) }
    }
}

/** "2 van 4 bezig" (of "uit" als de baan geen plekken heeft), met de eerstvolgende ETA op deze baan. */
fun laneSummary(l: Lane, running: List<RunningEncode>): Pair<String, String?> {
    val head = when {
        l.slots == 0 && l.running == 0 -> "uit"
        else -> "${l.running} van ${l.slots} bezig"
    } + if (l.paused) " · gepauzeerd" else ""
    val eta = running.mapNotNull { it.etaSeconds }.filter { it > 0 }.minOrNull()
    return head to eta?.let { "eerstvolgende klaar ${formatEta(it).removePrefix("nog ").let { t -> "over $t" }}" }
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
            MgButton(if (l.paused) "Hervatten" else "Pauzeren", { a.lane(l.lane, l.paused) }, small = true,
                icon = if (l.paused) R.drawable.lucide_play else R.drawable.lucide_pause)
        }
        if (l.paused) StatusBadge(PeilStatus.Gepauzeerd)
        MgProgress(if (l.slots > 0) l.running.toFloat() / l.slots else 0f, color = laneColor(l.lane), height = 6.dp,
            description = "${l.running} van ${l.slots} plekken bezig")
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
        CardHeader("Wachtrij", R.drawable.lucide_list_ordered, count = s.encoder.queued, trailing = {
            MgButton("Autopilot nu", a.autopilot, small = true)
        })
        Text(if (s.encoder.queued == 0) "Er wacht niets. De autopilot zet nieuw werk klaar zodra er iets te besparen valt."
            else "${s.encoder.queued} jobs wachten op een vrije plek.", style = Mg.type.small, color = Mg.colors.muted)
    }
}

/**
 * "Waar ging de ruimte heen": grafiekslots (nooit statuskleuren): Erbij `chart-1`, AV1-besparing `chart-2`,
 * Niet verklaard `chart-other` met arcering (PEIL_MIGRATIE §10.3). Nog niet gemeten = Onbekend, geen balk van 0.
 */
@Composable
private fun WhereDidSpaceGo(recent: UiState<DailySummary>, a: SpaceActions) {
    MgCard {
        CardHeader("Waar ging de ruimte heen", R.drawable.lucide_scale, trailing = {
            MgButton("Details", a.openHappened, kind = ButtonKind.Ghost, small = true)
        })
        when (recent) {
            UiState.Loading -> SkeletonLine(0.7f)
            is UiState.Error -> Text(recent.message, style = Mg.type.small, color = Mg.colors.muted)
            is UiState.Data -> {
                val st = recent.value.storage
                val date = runCatching { LocalDate.parse(recent.value.date) }.getOrNull()
                val label = if (recent.value.window != null) recentTitle(recent.value)
                else date?.let { formatDay(it).replaceFirstChar { c -> c.uppercase() } } ?: "Gisteren"
                Text("$label: netto ${formatBytes(st.netBytes, signed = true)}", style = Mg.type.bodyStrong, color = Mg.colors.text)
                val max = listOf(st.addedBytes, st.savedBytes, st.unexplainedBytes ?: 0).maxOf { abs(it) }.coerceAtLeast(1)
                SpaceBar("Erbij", "nieuwe bestanden en upgrades", st.addedBytes, max, Mg.peil.chart1, plus = true)
                SpaceBar("AV1-besparing", "bestanden kleiner gemaakt", st.savedBytes, max, Mg.peil.chart2, plus = false)
                val unexplained = st.unexplainedBytes
                if (unexplained == null) SpaceBar("Niet verklaard", "nog niet gemeten (de vrije ruimte wordt elk uur gemeten)",
                    null, max, Mg.peil.chartOther, plus = true, hatched = true)
                else SpaceBar("Niet verklaard", "verschil dat MediaGuard niet kan toewijzen (bv. andere apps op de pool)",
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
