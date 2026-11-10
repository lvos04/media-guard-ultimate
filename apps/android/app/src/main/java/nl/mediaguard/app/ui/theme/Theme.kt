package nl.mediaguard.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Kleuren van de app in Peil 1.1.0 (docs/ontwerp/PEIL_MIGRATIE.md §10.1, ADR-0018). De namen van vóór Peil blijven
 * (veel aanroepen), maar elke waarde komt uit [PeilTokens] (gegenereerd uit design/ontwerptaal/tokens.json).
 * Er is geen derde tekstniveau meer (`muted` = `faint`) en geen violet: violet betekent in Peil "een mens moet
 * goedkeuren" en komt in deze app niet voor. Nieuwe code leest [Mg.peil] met de tokennamen.
 */
@Immutable
data class MgColors(
    val dark: Boolean,
    val peil: PeilKleuren,
    val bg: Color,           // background
    val surface: Color,      // card
    val surface2: Color,     // secondary (secundaire knop, segment)
    val sunken: Color,       // muted
    val line: Color,         // border (alleen decoratief)
    val lineSoft: Color,     // border
    val lineStrong: Color,   // border-strong (bedienbare randen, stippelrand Onbekend)
    val text: Color,         // foreground
    val muted: Color,        // muted-foreground
    val faint: Color,        // muted-foreground (geen derde niveau)
    val accent: Color,       // primary
    val onAccent: Color,     // primary-foreground
    val ok: Color,           // healthy (alleen gemeten gezondheid)
    val warn: Color,         // degraded
    val bad: Color,          // down
    val info: Color,         // info (ook Gepauzeerd)
    val accentSoft: Color,   // accent (selectie, shadcn-betekenis)
    val okSoft: Color,       // healthy-bg
    val warnSoft: Color,     // degraded-bg
    val badSoft: Color,      // down-bg
    val infoSoft: Color,     // info-bg
    val neutralSoft: Color,  // unknown-bg
)

fun mgColors(dark: Boolean): MgColors {
    val p = if (dark) PeilTokens.Donker else PeilTokens.Licht
    return MgColors(
        dark = dark, peil = p,
        bg = p.background, surface = p.card, surface2 = p.secondary, sunken = p.muted,
        line = p.border, lineSoft = p.border, lineStrong = p.borderStrong,
        text = p.foreground, muted = p.mutedForeground, faint = p.mutedForeground,
        accent = p.primary, onAccent = p.primaryForeground,
        ok = p.healthy, warn = p.degraded, bad = p.down, info = p.info,
        accentSoft = p.accent, okSoft = p.healthyBg, warnSoft = p.degradedBg, badSoft = p.downBg, infoSoft = p.infoBg,
        neutralSoft = p.unknownBg,
    )
}

val DarkMg = mgColors(dark = true)
val LightMg = mgColors(dark = false)

/** Ruimte-schaal (Tailwind-basis 4 dp, zoals de web). */
object MgSpace {
    val s1 = 4.dp
    val s2 = 8.dp
    val s3 = 12.dp
    val s4 = 16.dp
    val s5 = 24.dp
    val s6 = 32.dp
}

/** Peil-vormen: kaart, knop en veld 8 dp; code/tag 4; dialoog en sheet 12; statuspil volledig rond. */
object MgShape {
    val card = RoundedCornerShape(PeilTokens.RADIUS_DP.dp)
    val control = RoundedCornerShape(PeilTokens.RADIUS_DP.dp)
    val segment = RoundedCornerShape(PeilTokens.RADIUS_DP.dp)
    val tag = RoundedCornerShape(4.dp)
    val sheet = RoundedCornerShape(12.dp)
    val sheetTop = RoundedCornerShape(topStart = 12.dp, topEnd = 12.dp)
    val pill = RoundedCornerShape(percent = 50)
}

/** Aanraakdoel op Android: 48 dp (Android-richtlijn, ruimer dan Peils 44 px; ADR-0016 punt 11). */
val TouchTarget = 48.dp

/**
 * Tekstrollen van Peil (§5) in sp. `small` en `meta` zijn allebei de kleinste rol (14/20): er is geen tekst onder
 * 14 sp meer. Cijfers zijn proportioneel; [cijfers] zet vaste breedte aan voor kolommen, tijden en aftellers.
 */
@Immutable
data class MgType(
    /** pagina 24/32 700 (één per scherm) */
    val title: TextStyle,
    /** sectie 20/28 700 */
    val section: TextStyle,
    /** titel 18/28 600 (kaarttitel) */
    val h2: TextStyle,
    /** body 16/24 400 */
    val body: TextStyle,
    /** body 16/24 600 (nadruk, knoplabel) */
    val bodyStrong: TextStyle,
    /** meta 14/20 400 */
    val small: TextStyle,
    /** meta 14/20 400 */
    val meta: TextStyle,
    /** waarde 30/36 600, proportionele cijfers */
    val kpi: TextStyle,
)

private fun rol(naam: String, gewicht: FontWeight? = null): TextStyle {
    val r = PeilTokens.rollen.getValue(naam)
    return TextStyle(
        fontFamily = FontFamily.Default, // systeemfont: Roboto op Android (Peil §5)
        fontWeight = gewicht ?: FontWeight(r.gewicht),
        fontSize = r.grootteSp.sp,
        lineHeight = r.regelSp.sp,
    )
}

val DefaultMgType = MgType(
    title = rol("pagina"),
    section = rol("sectie"),
    h2 = rol("titel"),
    body = rol("body"),
    bodyStrong = rol("body", FontWeight.SemiBold),
    small = rol("meta"),
    meta = rol("meta"),
    kpi = rol("waarde"),
)

/** Cijfers met vaste breedte (Peil `cijfers`): alleen voor kolommen, tijden en aftellers. */
val TextStyle.cijfers: TextStyle get() = copy(fontFeatureSettings = "tnum")

val LocalMgColors = staticCompositionLocalOf { DarkMg }
val LocalMgType = staticCompositionLocalOf { DefaultMgType }

/** Korte toegang: `Mg.colors.accent`, `Mg.peil.healthyInk`, `Mg.type.body`. */
object Mg {
    val colors: MgColors @Composable @ReadOnlyComposable get() = LocalMgColors.current
    val peil: PeilKleuren @Composable @ReadOnlyComposable get() = LocalMgColors.current.peil
    val type: MgType @Composable @ReadOnlyComposable get() = LocalMgType.current
}

/**
 * Material 3-rollen uit Peil (§10.1): nergens het standaardpaars van Material; secondary is inkt, tertiary info,
 * outline de sterke rand, error de gevaarkleur. Geen Material You (dynamische kleuren) en geen surfaceTint.
 */
fun peilColorScheme(c: MgColors): ColorScheme {
    val p = c.peil
    val base = if (c.dark) darkColorScheme() else lightColorScheme()
    return base.copy(
        primary = p.primary, onPrimary = p.primaryForeground, primaryContainer = p.accent, onPrimaryContainer = p.foreground,
        inversePrimary = if (c.dark) PeilTokens.Licht.primary else PeilTokens.Donker.primary,
        secondary = p.foreground, onSecondary = p.background, secondaryContainer = p.accent, onSecondaryContainer = p.foreground,
        tertiary = p.info, onTertiary = p.background, tertiaryContainer = p.infoBg, onTertiaryContainer = p.infoInk,
        background = p.background, onBackground = p.foreground,
        surface = p.card, onSurface = p.foreground, surfaceVariant = p.muted, onSurfaceVariant = p.mutedForeground,
        surfaceTint = Color.Transparent, inverseSurface = p.foreground, inverseOnSurface = p.background,
        error = p.destructive, onError = p.destructiveForeground, errorContainer = p.downBg, onErrorContainer = p.downInk,
        outline = p.borderStrong, outlineVariant = p.border, scrim = Color.Black,
        surfaceBright = p.card, surfaceDim = p.background,
        surfaceContainerLowest = p.background, surfaceContainerLow = p.card, surfaceContainer = p.card,
        surfaceContainerHigh = p.popover, surfaceContainerHighest = p.popover,
    )
}

private fun typography(t: MgType): Typography {
    val d = Typography()
    fun TextStyle.sys() = copy(fontFamily = FontFamily.Default)
    val label = t.bodyStrong
    return Typography(
        displayLarge = d.displayLarge.sys(), displayMedium = d.displayMedium.sys(), displaySmall = t.kpi,
        headlineLarge = d.headlineLarge.sys(), headlineMedium = d.headlineMedium.sys(), headlineSmall = t.title,
        titleLarge = t.section, titleMedium = t.h2, titleSmall = t.meta.copy(fontWeight = FontWeight.SemiBold),
        bodyLarge = t.body, bodyMedium = t.body, bodySmall = t.meta,
        labelLarge = label, labelMedium = t.meta.copy(fontWeight = FontWeight.SemiBold), labelSmall = t.meta,
    )
}

/** Volgt de donker/licht-stand van het systeem; bewust géén Material You-kleuren en geen themaknop. */
@Composable
fun MediaGuardTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val c = if (dark) DarkMg else LightMg
    CompositionLocalProvider(LocalMgColors provides c, LocalMgType provides DefaultMgType) {
        MaterialTheme(
            colorScheme = peilColorScheme(c),
            typography = typography(DefaultMgType),
            shapes = Shapes(
                extraSmall = RoundedCornerShape(4.dp), small = RoundedCornerShape(6.dp), medium = MgShape.card,
                large = MgShape.sheet, extraLarge = MgShape.sheet,
            ),
            content = content,
        )
    }
}
