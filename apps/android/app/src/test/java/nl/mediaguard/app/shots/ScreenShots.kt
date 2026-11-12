package nl.mediaguard.app.shots

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.Modifier
import app.cash.paparazzi.DeviceConfig
import app.cash.paparazzi.Paparazzi
import com.android.resources.Density
import com.android.resources.NightMode
import nl.mediaguard.app.PhoneState
import nl.mediaguard.app.data.AppTaal
import nl.mediaguard.app.data.Taal
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.data.UpdateInfo
import nl.mediaguard.app.ui.LibraryKind
import nl.mediaguard.app.ui.UiState
import nl.mediaguard.app.ui.nav.BottomBar
import nl.mediaguard.app.ui.nav.TopBar
import nl.mediaguard.app.ui.screens.ActivityActions
import nl.mediaguard.app.ui.screens.ActivityContent
import nl.mediaguard.app.ui.screens.ActivityTab
import nl.mediaguard.app.ui.screens.LibraryContent
import nl.mediaguard.app.ui.screens.LibraryItemContent
import nl.mediaguard.app.ui.screens.OverviewActions
import nl.mediaguard.app.ui.screens.OverviewContent
import nl.mediaguard.app.ui.screens.OverviewExtras
import nl.mediaguard.app.ui.screens.PairingContent
import nl.mediaguard.app.ui.screens.PairingReason
import nl.mediaguard.app.ui.screens.RequestSheetContent
import nl.mediaguard.app.ui.screens.RequestsActions
import nl.mediaguard.app.ui.screens.RequestsContent
import nl.mediaguard.app.ui.screens.RequestsTab
import nl.mediaguard.app.ui.screens.SettingsActions
import nl.mediaguard.app.ui.screens.SettingsContent
import nl.mediaguard.app.ui.screens.SettingsModel
import nl.mediaguard.app.ui.screens.SpaceActions
import nl.mediaguard.app.ui.screens.SpaceContent
import nl.mediaguard.app.ui.theme.MediaGuardTheme
import nl.mediaguard.app.ui.theme.Mg
import org.junit.After
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.time.LocalDate

/**
 * Schermafdrukken in Peil 1.1.0 (docs/ontwerp/PEIL_MIGRATIE.md §10–§11, ADR-0018): de echte composables met de
 * echte bovenbalk en ondernavigatie, voorbeeldgegevens uit [Sample] (verzonnen titels in de vorm van echte
 * API-antwoorden; geen NAS-, apparaat- of accountgegevens). Posters zijn leeg (geen netwerk).
 *
 * Referentieset (in de repo, `app/src/test/snapshots/images`): licht/donker × 393 dp (Pixel 5) en 360 dp, plus
 * grote letter (fontScale 1,3, licht, 393 dp) voor een deel van de schermen, en datzelfde deel in het Engels
 * (en-US, licht, 393 dp; ADR-0023). CI vergelijkt met
 * `:app:verifyPaparazziDebug`; na een bewuste wijziging opnieuw vastleggen met `:app:recordPaparazziDebug`.
 *
 * Met PEIL_AFDRUKKEN=1 komen er ook afdrukken op 2 px per dp (360, 393 en 393-volledig) voor
 * docs/screenshots/peil/android/ (scripts in die map); die horen niet bij de referentieset. Tijden in beeld volgen de
 * tijdzone van de test-JVM; build.gradle.kts zet die op UTC (lokaal gelijk aan de CI-runner).
 */
abstract class ScreenShots(
    private val dark: Boolean,
    device: DeviceConfig,
    /** Alleen de schermen met `groot = true` (grote letter: een deel, om de referentieset klein te houden). */
    private val subset: Boolean = false,
    /** Afdruk voor de documentatie (alleen met PEIL_AFDRUKKEN=1). */
    private val docs: Boolean = false,
    /** Taal van de app-teksten; de voorbeeldgegevens (teksten van de server) blijven Nederlands. */
    private val taal: AppTaal = AppTaal.Nl,
    /** Alleen de schermen met `smalGroot = true` (grote letter op 360 dp, volle lengte), op volle resolutie. */
    private val alleenSmalGroot: Boolean = false,
) {
    @get:Rule
    val paparazzi = Paparazzi(
        deviceConfig = device.copy(nightMode = if (dark) NightMode.NIGHT else NightMode.NOTNIGHT),
        // gemeten (KLEIN-18): op HEAD lokaal groen met 0,0 %; 0,01 % geeft een kleine marge. Een andere
        // titel of knoptekst (0,06 tot 0,47 %) maakt verify zo rood; 0,5 % liet die door (R-TECH-3)
        maxPercentDifference = 0.01,
        useDeviceResolution = docs || alleenSmalGroot,
    )

    @Before fun kiesTaal() { Taal.huidig = taal }
    @After fun terugNaarNederlands() { Taal.huidig = AppTaal.Nl }

    private val day = LocalDate.of(2026, 11, 7)
    private val today = LocalDate.of(2026, 11, 8)

    private fun gate(groot: Boolean, smalGroot: Boolean = false) {
        if (docs) assumeTrue("alleen met PEIL_AFDRUKKEN=1", System.getenv("PEIL_AFDRUKKEN") == "1")
        if (subset) assumeTrue("grote letter alleen voor een deel van de schermen", groot)
        if (alleenSmalGroot) assumeTrue("grote letter op 360 dp alleen voor Instellingen, Bibliotheek-detail en Aanvragen", smalGroot)
    }

    private fun shot(
        route: String,
        title: String? = null,
        groot: Boolean = false,
        smalGroot: Boolean = false,
        content: @Composable () -> Unit,
    ) {
        gate(groot, smalGroot)
        paparazzi.snapshot {
            MediaGuardTheme(dark = dark) {
                Column(Modifier.fillMaxSize().background(Mg.colors.bg)) {
                    TopBar(title, Sample.status.attention.count, Sample.status.attention.errors, "JV",
                        onBack = if (title != null) ({}) else null, onSearch = {}, onAttention = {}, onProfile = {})
                    Box(Modifier.weight(1f).fillMaxWidth()) { content() }
                    BottomBar(route) {}
                }
            }
        }
    }

    private fun bare(groot: Boolean = false, content: @Composable () -> Unit) {
        gate(groot)
        paparazzi.snapshot {
            MediaGuardTheme(dark = dark) { Box(Modifier.fillMaxSize().background(Mg.colors.bg)) { content() } }
        }
    }

    @Test fun overzicht() = shot("overview", groot = true) {
        OverviewContent(UiState.Data(Sample.status), 23, UiState.Data(Sample.recent), OverviewActions(), date = today)
    }

    @Test fun overzichtRustig() = shot("overview") {
        OverviewContent(UiState.Data(Sample.calmStatus), 0, UiState.Data(Sample.recent), OverviewActions(), date = today)
    }

    @Test fun overzichtNoodstop() = shot("overview") {
        OverviewContent(UiState.Data(Sample.stopStatus), 0, UiState.Data(Sample.recent), OverviewActions(), date = today)
    }

    @Test fun overzichtVerouderd() = shot("overview") {
        OverviewContent(UiState.Data(Sample.calmStatus), 0, UiState.Data(Sample.recent), OverviewActions(),
            OverviewExtras(staleSince = 1_762_600_000_000, staleError = "MediaGuard is niet bereikbaar. Controleer het adres en je verbinding.",
                phoneWarning = "Meldingen staan uit voor MediaGuard op deze telefoon", update = "MediaGuard 1.2.3 is uit; je server draait 1.2.1"),
            date = today)
    }

    @Test fun activiteitProblemen() = shot("activity", groot = true) {
        ActivityContent(ActivityTab.Problemen, UiState.Data(Sample.status), day, UiState.Data(Sample.summary), UiState.Loading,
            ActivityActions(), today)
    }

    @Test fun bibliotheekOntbrekend() = shot("library") {
        LibraryContent(LibraryKind.Series, UiState.Data(Sample.library.copy(total = 2, items = Sample.library.items.take(1))),
            "", {}, {}, {}, {}, {}, missingOnly = true)
    }

    @Test fun activiteit() = shot("activity") {
        ActivityContent(ActivityTab.Nu, UiState.Data(Sample.status), day, UiState.Data(Sample.summary), UiState.Loading,
            ActivityActions(), today)
    }

    @Test fun activiteitGebeurd() = shot("activity") {
        ActivityContent(ActivityTab.Gebeurd, UiState.Data(Sample.status), day, UiState.Data(Sample.summary), UiState.Loading,
            ActivityActions(), today)
    }

    @Test fun bibliotheek() = shot("library", groot = true) {
        LibraryContent(LibraryKind.Series, UiState.Data(Sample.library), "", {}, {}, {}, {}, {})
    }

    @Test fun bibliotheekDetail() = shot("library", title = tr("Bibliotheek", "Library"), smalGroot = true) {
        LibraryItemContent("Bergpas", UiState.Data(Sample.detail), { _, _ -> }, {})
    }

    @Test fun ruimte() = shot("space", groot = true) {
        SpaceContent(UiState.Data(Sample.status), UiState.Data(Sample.recent), SpaceActions())
    }

    @Test fun aanvragen() = shot("requests", smalGroot = true) {
        RequestsContent(RequestsTab.Ontdekken, "", UiState.Data(Sample.seerr), UiState.Data(Sample.mine), RequestsActions())
    }

    @Test fun aanvragenMijn() = shot("requests") {
        RequestsContent(RequestsTab.Mijn, "", UiState.Data(Sample.seerr), UiState.Data(Sample.mine), RequestsActions())
    }

    @Test fun aanvraagVenster() = bare {
        Box(Modifier.fillMaxSize().background(Mg.peil.popover)) {
            RequestSheetContent(UiState.Data(Sample.seerrDetail), mutableStateListOf(3), {}, {})
        }
    }

    @Test fun instellingen() = shot("settings", title = "") {
        SettingsContent(SettingsModel(true, 8 * 60 + 30, true, "mediaguard-ab12", "https://ntfy.sh",
            "https://mediaguard.thuis.lan", "jan", "1.2.1", serverVersion = "1.2.1",
            ntfyLastOkAt = 1_762_600_000_000, pushOnServer = true, lastContactAt = 1_762_600_000_000), SettingsActions())
    }

    @Test fun instellingenWaarschuwingen() = shot("settings", title = "", groot = true, smalGroot = true) {
        SettingsContent(SettingsModel(true, 8 * 60, true, "mediaguard-ab12", "https://ntfy.sh",
            "https://mediaguard.example.nl", "jan", "1.2.1", serverVersion = "1.2.1",
            phone = PhoneState(notificationsAllowed = false, batteryExempt = false),
            ntfyLastError = "ntfy-server niet bereikbaar",
            update = UpdateInfo("1.2.1", "1.2.1", latest = "1.2.3")), SettingsActions())
    }

    /** Koppelscherm (nieuw in de set): eerste keer, leeg. */
    @Test fun koppelen() = bare(groot = true) {
        PairingContent(PairingReason.New, false, "", {}, "", {}, busy = false, error = null, onScan = {}, onConnect = {},
            onRetryStored = {})
    }

    /** Koppelscherm: de sleutel is geweigerd en de vorige poging mislukte (Foutpaneel). */
    @Test fun koppelenGeweigerd() = bare {
        PairingContent(PairingReason.KeyRejected, false, "https://mediaguard.example.nl", {}, "mgk_voorbeeld", {},
            busy = false, error = "MediaGuard weigert deze sleutel (HTTP 401).", onScan = {}, onConnect = {}, onRetryStored = {})
    }
}

/** Pixel 5: 393 × 851 dp. */
private val P393: DeviceConfig = DeviceConfig.PIXEL_5

/** 360 × 851 dp (990 px bij 440 dpi), de smalste gangbare telefoon. */
private val P360: DeviceConfig = DeviceConfig.PIXEL_5.copy(screenWidth = 990)

/** Documentatie: 2 px per dp (xhdpi), 393 en 360 dp breed. */
private val D393: DeviceConfig = DeviceConfig.PIXEL_5.copy(screenWidth = 786, screenHeight = 1702, xdpi = 320, ydpi = 320,
    density = Density.XHIGH)
private val D360: DeviceConfig = D393.copy(screenWidth = 720)

/** Documentatie "volledig": 393 dp breed en 2 618 dp hoog (lange schermen in één afdruk). */
private val D393Hoog: DeviceConfig = D393.copy(screenHeight = 5236)

class DarkScreenShots : ScreenShots(dark = true, P393)
class LightScreenShots : ScreenShots(dark = false, P393)
class Dark360ScreenShots : ScreenShots(dark = true, P360)
class Light360ScreenShots : ScreenShots(dark = false, P360)
class LightGroteLetterScreenShots : ScreenShots(dark = false, P393.copy(fontScale = 1.3f), subset = true)

/** De app in het Engels (en-US): hetzelfde deel van de schermen als de grote letter. */
class EnLightScreenShots : ScreenShots(dark = false, P393, subset = true, taal = AppTaal.En)

/**
 * Grote letter (fontScale 1,3) op 360 dp in het Engels, op volle lengte (2 618 dp) en 2 px per dp: Instellingen met
 * waarschuwingen (sleutel en waarde in `KeyValue` plakken niet aan elkaar, ook onderaan) en Bibliotheek-detail (de
 * titel "Library" breekt niet midden in een woord). Bewaakt het herstel van R-ONTWERP-1/-2 (PEIL_REVIEW §12.3).
 * Ook Aanvragen (Discover): het label "Partial" in een kaart (PEIL_REVIEW §12.4, KLEIN-20).
 */
class EnGrootLight360HoogScreenShots : ScreenShots(dark = false, D360.copy(screenHeight = 5236, fontScale = 1.3f),
    taal = AppTaal.En, alleenSmalGroot = true)

class DocsDark393 : ScreenShots(dark = true, D393, docs = true)
class DocsLight393 : ScreenShots(dark = false, D393, docs = true)
class DocsDark360 : ScreenShots(dark = true, D360, docs = true)
class DocsLight360 : ScreenShots(dark = false, D360, docs = true)
class DocsDarkVolledig : ScreenShots(dark = true, D393Hoog, docs = true)
class DocsLightVolledig : ScreenShots(dark = false, D393Hoog, docs = true)
class DocsLightGroteLetter : ScreenShots(dark = false, D393.copy(fontScale = 1.3f), subset = true, docs = true)
