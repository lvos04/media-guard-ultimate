package nl.mediaguard.app.ui.screens

import android.Manifest
import android.app.TimePickerDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LifecycleResumeEffect
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonPrimitive
import nl.mediaguard.app.BuildConfigInfo
import nl.mediaguard.app.MediaGuardApp
import nl.mediaguard.app.PhoneChecks
import nl.mediaguard.app.PhoneState
import nl.mediaguard.app.data.UpdateInfo
import nl.mediaguard.app.data.formatClockMillis
import nl.mediaguard.app.data.friendlyMessage
import nl.mediaguard.app.ui.Banner
import nl.mediaguard.app.ui.ButtonKind
import nl.mediaguard.app.ui.CardHeader
import nl.mediaguard.app.ui.ConfirmDialog
import nl.mediaguard.app.ui.KeyValue
import nl.mediaguard.app.ui.MgButton
import nl.mediaguard.app.ui.MgCard
import nl.mediaguard.app.ui.MgSwitch
import nl.mediaguard.app.ui.PageHeader
import nl.mediaguard.app.ui.ScreenList
import nl.mediaguard.app.ui.Tone
import nl.mediaguard.app.ui.Melding
import nl.mediaguard.app.ui.theme.TouchTarget
import nl.mediaguard.app.R
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.ui.unit.dp
import nl.mediaguard.app.ui.theme.Mg
import nl.mediaguard.app.work.Alerts
import nl.mediaguard.app.work.Schedules

/** Wat het instellingenscherm toont; los van Android zodat het ook in een screenshot-test kan. */
data class SettingsModel(
    val daily: Boolean,
    val minutes: Int,
    val ntfy: Boolean,
    val ntfyTopic: String?,
    val ntfyServer: String?,
    val serverUrl: String?,
    val user: String?,
    val appVersion: String,
    val serverVersion: String? = null,
    val phone: PhoneState = PhoneState(),
    /** Laatste geslaagde ophaalactie bij ntfy (epoch ms), en de laatste fout. */
    val ntfyLastOkAt: Long? = null,
    val ntfyLastError: String? = null,
    /** MediaGuard heeft het telefoonkanaal aangezet na het koppelen. */
    val pushOnServer: Boolean = false,
    /** Aanzetten is nog niet gelukt; de app probeert het vanzelf opnieuw. */
    val pushPending: Boolean = false,
    val lastContactAt: Long? = null,
    val nextDailyAt: Long? = null,
    val update: UpdateInfo? = null,
    val testing: Boolean = false,
)

class SettingsActions(
    val setDaily: (Boolean) -> Unit = {},
    val pickTime: () -> Unit = {},
    val setNtfy: (Boolean) -> Unit = {},
    val unpair: () -> Unit = {},
    val testNotification: () -> Unit = {},
    /** Telefoonkanaal in MediaGuard (weer) aanzetten (POST /app/paired). */
    val enablePush: () -> Unit = {},
    val fixNotifications: () -> Unit = {},
    val fixBattery: () -> Unit = {},
    val openUrl: (String) -> Unit = {},
)

@Composable
fun SettingsScreen(user: String?, serverVersion: String?, update: UpdateInfo?, showMessage: (String) -> Unit, onUnpaired: () -> Unit) {
    val context = LocalContext.current
    val app = context.applicationContext as MediaGuardApp
    val s = app.settings
    val st = app.state
    val scope = rememberCoroutineScope()
    var daily by remember { mutableStateOf(s.dailyEnabled) }
    var minutes by remember { mutableStateOf(s.dailyMinutes) }
    var ntfy by remember { mutableStateOf(s.ntfyEnabled) }
    var topic by remember { mutableStateOf(s.ntfyTopic) }
    var confirmUnpair by remember { mutableStateOf(false) }
    var phone by remember { mutableStateOf(PhoneChecks.read(context)) }
    var testing by remember { mutableStateOf(false) }
    var tick by remember { mutableLongStateOf(0L) }
    LifecycleResumeEffect(Unit) { phone = PhoneChecks.read(context); tick++; onPauseOrDispose { } }

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) PhoneChecks.openNotificationSettings(context)
        phone = PhoneChecks.read(context)
    }

    fun test() {
        if (!phone.notificationsAllowed) { showMessage("Meldingen staan uit voor MediaGuard. Zet ze eerst aan."); return }
        val repo = app.repository() ?: return
        testing = true
        scope.launch {
            val params = s.ntfyTopic?.let { mapOf("topic" to JsonPrimitive(it)) } ?: emptyMap()
            val result = runCatching { repo.control("test_notification", params) }
            testing = false
            result.onSuccess { r ->
                r.ntfy?.let { info -> if (info.topic.isNotBlank() && info.topic != s.ntfyTopic) { s.saveNtfy(info); topic = info.topic; ntfy = true } }
                // Staat het kanaal in MediaGuard uit (bv. in de web uitgezet), dan zegt Instellingen dat eerlijk,
                // met een knop om het weer aan te zetten.
                if (r.ntfy?.enabled == false) st.pushRegisteredAt = 0
                if (r.ok && s.ntfyEnabled) {
                    Schedules.apply(context, s, st)
                    Schedules.pollNow(context)
                    showMessage("${r.message}. Hij komt binnen een minuut op deze telefoon binnen.")
                } else {
                    Alerts.localTest(context, "Meldingen op deze telefoon werken. ${r.message}")
                    showMessage(r.message.ifBlank { "Testbericht niet verstuurd" })
                }
            }.onFailure { e ->
                Alerts.localTest(context, "Meldingen op deze telefoon werken, maar MediaGuard kon geen testbericht sturen.")
                showMessage("Testbericht via MediaGuard mislukt: ${e.friendlyMessage()}")
            }
            tick++
        }
    }

    // tick laat de tijden opnieuw lezen na een test of terugkeer naar dit scherm
    val model = remember(daily, minutes, ntfy, topic, phone, testing, tick, user, serverVersion, update) {
        SettingsModel(daily, minutes, ntfy, topic, s.ntfyServer, s.serverUrl, user, BuildConfigInfo.versionName(context),
            serverVersion = serverVersion, phone = phone,
            ntfyLastOkAt = st.ntfyLastOkAt.takeIf { it > 0 }, ntfyLastError = st.ntfyLastError,
            pushOnServer = st.pushRegisteredAt > 0, pushPending = st.pushPending, lastContactAt = st.lastContactAt.takeIf { it > 0 },
            nextDailyAt = st.dailyTargetAt.takeIf { it > 0 && daily }, update = update, testing = testing)
    }
    SettingsContent(
        model,
        SettingsActions(
            setDaily = { daily = it; s.dailyEnabled = it; Schedules.apply(context, s, st, rescheduleDaily = true); tick++ },
            pickTime = {
                TimePickerDialog(context, { _, h, m ->
                    minutes = h * 60 + m; s.dailyMinutes = minutes
                    Schedules.apply(context, s, st, rescheduleDaily = true); tick++
                }, minutes / 60, minutes % 60, true).show()
            },
            setNtfy = { ntfy = it; s.ntfyEnabled = it; Schedules.apply(context, s, st) },
            unpair = { confirmUnpair = true },
            testNotification = ::test,
            enablePush = { app.registerPush { msg -> topic = s.ntfyTopic; ntfy = s.ntfyEnabled; showMessage(msg); tick++ } },
            fixNotifications = {
                val needsPermission = Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(
                    context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                if (needsPermission) permission.launch(Manifest.permission.POST_NOTIFICATIONS)
                else PhoneChecks.openNotificationSettings(context)
            },
            fixBattery = { PhoneChecks.requestBatteryExemption(context) },
            openUrl = { url -> runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } },
        ),
    )
    if (confirmUnpair) ConfirmDialog(
        title = "Ontkoppelen?",
        text = "De app vergeet de sleutel en het adres. Meldingen stoppen tot je opnieuw koppelt.",
        confirmLabel = "Ontkoppelen", danger = true,
        onConfirm = { confirmUnpair = false; app.unpair(); onUnpaired() },
        onDismiss = { confirmUnpair = false },
    )
}

private const val RELEASES = "https://github.com/lvos04/media-guard-ultimate/releases/latest"

@Composable
fun SettingsContent(m: SettingsModel, a: SettingsActions) {
    ScreenList {
        item { PageHeader("Instellingen", "Meldingen en koppeling van deze telefoon") }
        item { PhoneCard(m, a) }
        item {
            MgCard {
                CardHeader("Dagoverzicht", R.drawable.lucide_bell)
                SwitchRow("Elke dag een melding", "Wat er gisteren vervangen, gedownload en aangevraagd is.", m.daily, a.setDaily)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Tijdstip", style = Mg.type.body.copy(fontWeight = FontWeight.Medium), color = Mg.colors.text)
                        m.nextDailyAt?.let { Text("Volgende: ${formatClockMillis(it)}", style = Mg.type.meta, color = Mg.colors.muted) }
                    }
                    MgButton("%02d:%02d".format(m.minutes / 60, m.minutes % 60), a.pickTime, small = true, enabled = m.daily,
                        contentDescription = "Tijdstip %02d:%02d wijzigen".format(m.minutes / 60, m.minutes % 60))
                }
            }
        }
        item {
            MgCard {
                CardHeader("Directe meldingen (ntfy)", R.drawable.lucide_bell_ring)
                Text(if (m.ntfyTopic.isNullOrBlank()) "Niet ingesteld. Tik op Aanzetten in MediaGuard, of koppel de app opnieuw: de koppelcode neemt het kanaal mee."
                    else "Kanaal ${m.ntfyTopic} op ${m.ntfyServer ?: "https://ntfy.sh"}. De app kijkt elke 15 minuten naar nieuwe berichten.",
                    style = Mg.type.small, color = Mg.colors.muted)
                SwitchRow("Meldingen ophalen", null, m.ntfy, a.setNtfy, enabled = !m.ntfyTopic.isNullOrBlank())
                if (!m.ntfyTopic.isNullOrBlank()) {
                    KeyValue("Laatst opgehaald", m.ntfyLastOkAt?.let { formatClockMillis(it) } ?: "nog niet")
                    m.ntfyLastError?.let { Melding(Tone.Bad, R.drawable.lucide_octagon_x, "Laatste poging mislukt", it) }
                    KeyValue("In MediaGuard", when {
                        m.pushOnServer -> "aan voor deze telefoon"
                        m.pushPending -> "wordt aangezet zodra MediaGuard bereikbaar is"
                        else -> "uit"
                    })
                }
                if (!m.pushOnServer) MgButton("Aanzetten in MediaGuard", a.enablePush, kind = ButtonKind.Secondary, small = true)
                MgButton(if (m.testing) "Testmelding wordt verstuurd…" else "Testmelding", a.testNotification,
                    enabled = !m.testing, modifier = Modifier.fillMaxWidth())
            }
        }
        item {
            MgCard {
                CardHeader("Koppeling", R.drawable.lucide_link_2)
                KeyValue("Server", m.serverUrl ?: "—")
                KeyValue("Gebruiker", m.user ?: "—")
                KeyValue("Laatste contact", m.lastContactAt?.let { formatClockMillis(it) } ?: "—")
                MgButton("Ontkoppelen", a.unpair, kind = ButtonKind.Danger, modifier = Modifier.fillMaxWidth())
            }
        }
        item {
            MgCard {
                CardHeader("Over", R.drawable.lucide_info)
                KeyValue("App", "MediaGuard Android ${m.appVersion}")
                KeyValue("Server", m.serverVersion?.let { "MediaGuard $it" } ?: "—")
                val u = m.update
                u?.serverUpdate?.let { KeyValue("Nieuwste versie", it) }
                u?.headline()?.let { line ->
                    Banner(line, Tone.Info, R.drawable.lucide_download,
                        sub = if (u.appUpdate != null) "Nieuwe app: via Obtainium of de GitHub-release." else "Server bijwerken: in TrueNAS de app updaten.",
                        action = { MgButton("Release", { a.openUrl(u.link ?: RELEASES) }, small = true) })
                }
                if (u != null && u.serverVersion != null && nl.mediaguard.app.data.Versions.newer(u.appVersion, u.serverVersion))
                    Text("Deze app is nieuwer dan je server. Werk MediaGuard bij voor alle functies.", style = Mg.type.meta, color = Mg.colors.muted)
                KeyValue("Iconen", "Lucide — ISC-licentie")
            }
        }
    }
}

@Composable
private fun PhoneCard(m: SettingsModel, a: SettingsActions) {
    if (m.phone.notificationsAllowed && m.phone.batteryExempt) return
    MgCard {
        CardHeader("Meldingen op deze telefoon", R.drawable.lucide_bell_off)
        if (!m.phone.notificationsAllowed) Banner("Meldingen staan uit voor MediaGuard", Tone.Info, R.drawable.lucide_bell_off,
            sub = "Je krijgt dan geen dagoverzicht en geen waarschuwingen.",
            action = { MgButton("Aanzetten", a.fixNotifications, small = true) })
        if (!m.phone.batteryExempt) Banner("Batterijbeperking staat aan", Tone.Info, R.drawable.lucide_battery_warning,
            sub = "Android kan het ophalen van meldingen dan uren uitstellen.",
            action = { MgButton("Uitzondering", a.fixBattery, small = true) })
    }
}

/** Schakelregel: titel + uitleg en een schakelaar; uit = `disabled-foreground` (nooit doorzichtigheid). */
@Composable
private fun SwitchRow(title: String, sub: String?, checked: Boolean, onChange: (Boolean) -> Unit, enabled: Boolean = true) {
    Row(Modifier.heightIn(min = TouchTarget), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = Mg.type.body.copy(fontWeight = FontWeight.Medium), color = if (enabled) Mg.colors.text else Mg.peil.disabledForeground)
            if (sub != null) Text(sub, style = Mg.type.meta, color = Mg.colors.muted)
        }
        Spacer(Modifier.width(8.dp))
        MgSwitch(checked, onChange, enabled = enabled)
    }
}
