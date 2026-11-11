package nl.mediaguard.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.activity.compose.rememberLauncherForActivityResult
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.launch
import nl.mediaguard.app.MediaGuardApp
import nl.mediaguard.app.R
import nl.mediaguard.app.data.ApiClient
import nl.mediaguard.app.data.ApiRepository
import nl.mediaguard.app.data.CleartextBlocked
import nl.mediaguard.app.data.Pairing
import nl.mediaguard.app.data.PairingPayload
import nl.mediaguard.app.data.friendlyMessage
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.ui.ButtonKind
import nl.mediaguard.app.ui.Melding
import nl.mediaguard.app.ui.MgButton
import nl.mediaguard.app.ui.Tone
import nl.mediaguard.app.ui.theme.Mg
import nl.mediaguard.app.ui.theme.MgSpace

/** Waarom het koppelscherm (opnieuw) verschijnt. */
enum class PairingReason { New, KeyRejected, StorageReset }

fun pairingReasonText(reason: PairingReason, volatileOnly: Boolean = false): String? = when (reason) {
    PairingReason.New -> null
    PairingReason.KeyRejected -> tr("MediaGuard weigert de sleutel van deze app. Hij is ingetrokken of verlopen, bijvoorbeeld ", "MediaGuard rejects the key of this app. It was revoked or has expired, for example ") +
        tr("omdat er een nieuwe koppelcode is gemaakt. Scan de nieuwe koppelcode.", "because a new pairing code was made. Scan the new pairing code.")
    PairingReason.StorageReset -> tr("De opgeslagen koppeling op deze telefoon was beschadigd (bijvoorbeeld na een systeemupdate ", "The saved pairing on this phone was damaged (for example after a system update ") +
        tr("of het overzetten naar een nieuwe telefoon) en is gewist. Koppel de app opnieuw.", "or a move to a new phone) and has been erased. Pair the app again.") +
        if (volatileOnly) tr(" Versleuteld opslaan lukt op dit toestel niet: de koppeling blijft alleen bewaard tot je de app sluit.", " Encrypted storage does not work on this device: the pairing is only kept until you close the app.") else ""
}

/** "http://host" → "https://host" (zelfde poort), voor een koppelcode die per ongeluk http:// bevat. */
fun httpsVariant(url: String): String? = if (url.startsWith("http://")) "https://" + url.removePrefix("http://") else null

@Composable
fun PairingScreen(reason: PairingReason = PairingReason.New, onPaired: () -> Unit) {
    val context = LocalContext.current
    val app = context.applicationContext as MediaGuardApp
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(app.settings.serverUrl ?: "") }
    var key by remember { mutableStateOf("") }
    var ntfy by remember { mutableStateOf<PairingPayload?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun connect(payload: PairingPayload) {
        busy = true; error = null
        scope.launch {
            suspend fun test(p: PairingPayload) = runCatching { ApiRepository(ApiClient.create(p.url, p.key)).me() }
            var used = payload
            var result = test(payload)
            // http:// naar een publiek adres wordt geweigerd (sleutel onversleuteld over internet): probeer https://
            val https = httpsVariant(payload.url)
            if (result.exceptionOrNull() is CleartextBlocked && https != null) {
                val second = test(payload.copy(url = https))
                if (second.isSuccess) { used = payload.copy(url = https); result = second }
            }
            busy = false
            result.onSuccess { me ->
                app.completePairing(used, me.role)
                onPaired()
            }.onFailure { error = it.friendlyMessage() }
        }
    }

    fun retryStored() {
        busy = true; error = null
        scope.launch {
            error = app.retryCurrentKey()
            busy = false
            if (error == null) onPaired()
        }
    }

    val scanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@rememberLauncherForActivityResult
        val p = Pairing.parse(text)
        if (p == null) error = tr("Dit is geen koppelcode van MediaGuard.", "This is not a MediaGuard pairing code.") else { url = p.url; key = p.key; ntfy = p; connect(p) }
    }

    PairingContent(
        reason = reason, volatileOnly = app.settings.volatileOnly, url = url, onUrl = { url = it }, key = key,
        onKey = { key = it }, busy = busy, error = error,
        onScan = {
            scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setPrompt(tr("Scan de koppelcode van MediaGuard", "Scan the MediaGuard pairing code")).setBeepEnabled(false).setOrientationLocked(false))
        },
        onConnect = {
            val u = Pairing.normalizeUrl(url)
            if (u == null || key.isBlank()) error = tr("Vul een geldig adres en een API-sleutel in.", "Enter a valid address and an API key.")
            else connect(PairingPayload(url = u, key = key.trim(), ntfy = ntfy?.ntfy))
        },
        onRetryStored = ::retryStored,
    )
}

/**
 * Het koppelscherm zelf, los van Android (ook voor de schermafdrukken). Peil: de reden waarom het scherm verschijnt
 * als melding (sleutel geweigerd = gemeten afwijking, `octagon-x`; opslag gewist = info), een fout als Foutpaneel,
 * knoppen van 48 dp en velden met een sterke rand.
 */
@Composable
fun PairingContent(
    reason: PairingReason,
    volatileOnly: Boolean,
    url: String,
    onUrl: (String) -> Unit,
    key: String,
    onKey: (String) -> Unit,
    busy: Boolean,
    error: String?,
    onScan: () -> Unit,
    onConnect: () -> Unit,
    onRetryStored: () -> Unit,
) {
    val p = Mg.peil
    val fieldColors = OutlinedTextFieldDefaults.colors(
        focusedBorderColor = p.ring, unfocusedBorderColor = p.input, focusedLabelColor = p.foreground,
        unfocusedLabelColor = p.mutedForeground, focusedTextColor = p.foreground, unfocusedTextColor = p.foreground,
        cursorColor = p.primary, focusedContainerColor = p.card, unfocusedContainerColor = p.card,
        focusedPlaceholderColor = p.mutedForeground, unfocusedPlaceholderColor = p.mutedForeground,
    )
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = MgSpace.s4, vertical = MgSpace.s5),
        verticalArrangement = Arrangement.spacedBy(MgSpace.s4),
    ) {
        Text(tr("MediaGuard koppelen", "Pair MediaGuard"), style = Mg.type.title, color = p.foreground, modifier = Modifier.semantics { heading() })
        pairingReasonText(reason, volatileOnly)?.let {
            if (reason == PairingReason.KeyRejected) Melding(Tone.Bad, R.drawable.lucide_octagon_x, tr("De sleutel is geweigerd", "The key was rejected"), it)
            else Melding(Tone.Info, R.drawable.lucide_info, tr("Opnieuw koppelen", "Pair again"), it)
        }
        Text(
            tr("Open MediaGuard in je browser → Instellingen → Koppelingen → Android-app koppelen, en scan de QR-code.", "Open MediaGuard in your browser → Settings → Connections → Pair the Android app, and scan the QR code."),
            style = Mg.type.body, color = p.mutedForeground,
        )
        MgButton(tr("QR-code scannen", "Scan QR code"), onScan, kind = ButtonKind.Primary, icon = R.drawable.lucide_scan_qr_code,
            enabled = !busy, modifier = Modifier.fillMaxWidth())
        Text(tr("Of handmatig", "Or manually"), style = Mg.type.h2, color = p.foreground, modifier = Modifier.semantics { heading() })
        OutlinedTextField(url, onUrl, label = { Text(tr("Adres van MediaGuard", "MediaGuard address")) },
            placeholder = { Text("https://mediaguard.example.nl") }, singleLine = true, textStyle = Mg.type.body,
            colors = fieldColors, shape = androidx.compose.foundation.shape.RoundedCornerShape(8.dp),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
        OutlinedTextField(key, onKey, label = { Text(tr("API-sleutel (mgk_…)", "API key (mgk_…)")) }, singleLine = true, textStyle = Mg.type.body,
            colors = fieldColors, shape = androidx.compose.foundation.shape.RoundedCornerShape(8.dp),
            visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
        MgButton(tr("Verbinden", "Connect"), onConnect, enabled = !busy, modifier = Modifier.fillMaxWidth())
        if (reason == PairingReason.KeyRejected) MgButton(tr("Opgeslagen sleutel opnieuw proberen", "Try saved key again"), onRetryStored, enabled = !busy,
            modifier = Modifier.fillMaxWidth())
        if (busy) Row(verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(24.dp), color = p.primary, strokeWidth = 3.dp)
            Spacer(Modifier.width(MgSpace.s2))
            Text(tr("Verbinden…", "Connecting…"), style = Mg.type.body, color = p.foreground)
        }
        error?.let { Melding(Tone.Bad, R.drawable.lucide_octagon_x, tr("Koppelen is mislukt", "Pairing failed"), it) }
        Text(
            tr("De sleutel wordt versleuteld op dit toestel bewaard en alleen naar jouw MediaGuard gestuurd.", "The key is stored encrypted on this device and only sent to your MediaGuard."),
            style = Mg.type.meta, color = p.mutedForeground,
        )
    }
}
