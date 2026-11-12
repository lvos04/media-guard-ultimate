package nl.mediaguard.app.ui.nav

import androidx.annotation.DrawableRes
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import nl.mediaguard.app.R
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.ui.PeilIcon
import nl.mediaguard.app.ui.theme.Mg
import nl.mediaguard.app.ui.theme.MgShape
import nl.mediaguard.app.ui.theme.TouchTarget
import nl.mediaguard.app.ui.theme.cijfers

/** Onderste balk zoals de web op de telefoon: vijf onderdelen, dezelfde lucide-iconen als de web-navigatie. */
data class MainTab(val route: String, private val nl: String, private val en: String, @DrawableRes val icon: Int) {
    /** Het woord in de taal van de app; de lijst hieronder wordt maar één keer gemaakt. */
    val label: String get() = tr(nl, en)
}

val MAIN_TABS = listOf(
    MainTab("overview", "Overzicht", "Overview", R.drawable.lucide_layout_dashboard),
    MainTab("activity", "Activiteit", "Activity", R.drawable.lucide_radar),
    MainTab("library", "Bibliotheek", "Library", R.drawable.lucide_library),
    MainTab("space", "Ruimte", "Space", R.drawable.lucide_hard_drive_download),
    MainTab("requests", "Aanvragen", "Requests", R.drawable.lucide_inbox),
)

private fun baseOf(route: String) = route.substringBefore('?').substringBefore('/')

/**
 * Ondernavigatie (Peil AppSchil < 1024): 64 dp hoog, icoon 24 dp + woord 14 sp. Gekozen = `accent`-vlak achter het
 * icoon, een balk van 4 dp bovenaan en vet woord in inkt (niet alleen kleur). De items zijn zo breed als hun woord
 * (zoals de web, ADR-0017 punt 9), zodat "Bibliotheek" op 320–360 dp heel blijft. Bij een grote systeemletter
 * groeien de woorden mee tot de balk vol is; daarna blijven ze zo groot als past, nooit kleiner dan 14 sp zonder
 * vergroting (ADR-0018 punt 6).
 */
@Composable
fun BottomBar(route: String, onSelect: (String) -> Unit) {
    val current = baseOf(route)
    val p = Mg.peil
    val measurer = rememberTextMeasurer()
    val density = LocalDensity.current
    Column(Modifier.fillMaxWidth().background(p.card).windowInsetsPadding(WindowInsets.navigationBars)) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(p.border))
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val base = Mg.type.meta.copy(fontWeight = FontWeight.Bold)
            val itemPad = with(density) { 8.dp.roundToPx() }
            val minItem = with(density) { TouchTarget.roundToPx() }
            val needed = MAIN_TABS.sumOf { maxOf(measurer.measure(it.label, base).size.width + itemPad, minItem) }
            val avail = constraints.maxWidth
            // schaal alleen omlaag als het niet past, en nooit onder 14 sp zonder systeemvergroting
            val factor = if (needed > avail) (avail.toFloat() / needed).coerceAtLeast(1f / density.fontScale.coerceAtLeast(1f)) else 1f
            val size = (base.fontSize.value * factor).sp
            Row(Modifier.fillMaxWidth().heightIn(min = 64.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
                MAIN_TABS.forEach { tab ->
                    val on = current == tab.route
                    Column(
                        Modifier.widthIn(min = TouchTarget).heightIn(min = 64.dp)
                            .selectable(on, role = Role.Tab) { onSelect(tab.route) }
                            .padding(horizontal = 4.dp),
                        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Top,
                    ) {
                        Box(Modifier.width(32.dp).height(4.dp).clip(RoundedCornerShape(bottomStart = 2.dp, bottomEnd = 2.dp))
                            .background(if (on) p.primary else androidx.compose.ui.graphics.Color.Transparent))
                        Spacer(Modifier.height(6.dp))
                        Box(Modifier.width(48.dp).height(28.dp).clip(MgShape.pill).background(if (on) p.accent else androidx.compose.ui.graphics.Color.Transparent),
                            contentAlignment = Alignment.Center) {
                            PeilIcon(tab.icon, if (on) p.primary else p.mutedForeground, size = 24.dp)
                        }
                        Spacer(Modifier.height(2.dp))
                        Text(tab.label, style = Mg.type.meta.copy(fontSize = size, fontWeight = if (on) FontWeight.Bold else FontWeight.Medium),
                            color = if (on) p.foreground else p.mutedForeground, textAlign = TextAlign.Center, softWrap = false)
                        Spacer(Modifier.height(4.dp))
                    }
                }
            }
        }
    }
}

/**
 * Bovenbalk: links het merk (of terug + titel op een subpagina), rechts zoeken, de aandacht-teller (inkt, nooit
 * rood; met de telling in de naam) en de profielknop met initialen (opent Instellingen). Alle doelen 48 dp.
 */
@Composable
fun TopBar(
    title: String?,
    attentionCount: Int,
    attentionErrors: Int,
    initials: String,
    onBack: (() -> Unit)?,
    onSearch: () -> Unit,
    onAttention: () -> Unit,
    onProfile: () -> Unit,
) {
    val p = Mg.peil
    val measurer = rememberTextMeasurer()
    val density = LocalDensity.current
    BoxWithConstraints(Modifier.fillMaxWidth().background(p.background).windowInsetsPadding(WindowInsets.statusBars)) {
        // Wat past er naast elkaar? Eerst valt de merknaam weg (het logo blijft), dan het woord "Aandacht" (icoon en
        // telling blijven, de volledige naam staat in de toegankelijke tekst). Zo breekt niets midden in een woord.
        val brandStyle = Mg.type.bodyStrong
        val pillStyle = Mg.type.meta.copy(fontWeight = FontWeight.SemiBold)
        fun w(dp: Dp) = with(density) { dp.roundToPx() }
        val brandText = measurer.measure("MediaGuard", brandStyle).size.width
        val word = measurer.measure(tr("Aandacht", "Attention"), pillStyle).size.width
        val count = measurer.measure("$attentionCount", pillStyle).size.width
        val pillCore = w(2.dp + 20.dp + 18.dp + 6.dp + 6.dp + 14.dp + 2.dp) + count
        val fixed = w(8.dp + TouchTarget + TouchTarget) + (if (onBack != null) w(TouchTarget) else w(12.dp + 32.dp))
        val avail = constraints.maxWidth
        val pillFull = if (attentionCount > 0) pillCore + word else 0
        val pillShort = if (attentionCount > 0) pillCore else 0
        // Op een subpagina telt de titel mee: past hij niet naast het hele woord, dan valt het woord weg (niet de titel
        // midden in een woord afbreken, zoals "Bibliothe|ek" op 360 dp of "Libra|ry" met grote letter).
        val titleText = if (onBack != null && !title.isNullOrEmpty()) measurer.measure(title, Mg.type.h2).size.width + w(8.dp) else 0
        val showWord = attentionCount > 0 && fixed + pillFull + titleText <= avail
        val showBrand = onBack == null && fixed + (if (showWord) pillFull else pillShort) + w(8.dp) + brandText <= avail
        Row(Modifier.fillMaxWidth().heightIn(min = 64.dp).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            if (onBack != null) {
                BarIcon(R.drawable.lucide_arrow_left, tr("Terug", "Back"), onBack)
                Text(title ?: "", style = Mg.type.h2, color = p.foreground, modifier = Modifier.weight(1f).semantics { heading() })
            } else {
                Row(Modifier.weight(1f).padding(start = 12.dp).semantics(mergeDescendants = true) { contentDescription = "MediaGuard" },
                    verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(32.dp).clip(MgShape.control).background(p.primary), contentAlignment = Alignment.Center) {
                        PeilIcon(R.drawable.lucide_shield_check, p.primaryForeground, size = 20.dp)
                    }
                    if (showBrand) {
                        Spacer(Modifier.width(8.dp))
                        Text("MediaGuard", style = brandStyle, color = p.foreground, softWrap = false)
                    }
                }
            }
            BarIcon(R.drawable.lucide_search, tr("Zoeken in de bibliotheek", "Search the library"), onSearch)
            if (attentionCount > 0) {
                val name = if (attentionCount == 1) tr("1 ding wacht op jou", "1 thing is waiting for you") else tr("$attentionCount dingen wachten op jou", "$attentionCount things are waiting for you")
                val errors = if (attentionErrors > 0) tr(", waarvan $attentionErrors met een fout", ", $attentionErrors with an error") else ""
                Row(
                    Modifier.padding(horizontal = 2.dp).heightIn(min = TouchTarget)
                        .clickable(role = Role.Button, onClick = onAttention)
                        .clearAndSetSemantics { contentDescription = tr("Aandacht $attentionCount: $name$errors", "Attention $attentionCount: $name$errors"); role = Role.Button }
                        .padding(vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(
                        Modifier.heightIn(min = 36.dp).clip(MgShape.pill).background(p.card).border(1.dp, p.borderStrong, MgShape.pill)
                            .padding(horizontal = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        PeilIcon(R.drawable.lucide_list_checks, p.foreground, size = 18.dp)
                        if (showWord) {
                            Spacer(Modifier.width(6.dp))
                            Text(tr("Aandacht", "Attention"), style = pillStyle, color = p.foreground, softWrap = false)
                        }
                        Spacer(Modifier.width(6.dp))
                        Box(Modifier.clip(MgShape.pill).background(p.foreground).padding(horizontal = 7.dp)) {
                            Text("$attentionCount", style = pillStyle.copy(fontWeight = FontWeight.Bold).cijfers, color = p.background,
                                softWrap = false)
                        }
                    }
                }
            }
            Box(
                Modifier.size(TouchTarget).clip(CircleShape).clickable(role = Role.Button, onClick = onProfile)
                    .clearAndSetSemantics { contentDescription = tr("$initials, profiel en instellingen", "$initials, profile and settings"); role = Role.Button },
                contentAlignment = Alignment.Center,
            ) {
                Box(Modifier.size(40.dp).clip(CircleShape).background(p.secondary).border(1.dp, p.borderStrong, CircleShape),
                    contentAlignment = Alignment.Center) {
                    Text(initials, style = Mg.type.meta.copy(fontWeight = FontWeight.Bold), color = p.foreground, softWrap = false)
                }
            }
        }
    }
}

@Composable
private fun BarIcon(@DrawableRes icon: Int, label: String, onClick: () -> Unit) {
    Box(Modifier.size(TouchTarget).clip(CircleShape).clickable(role = Role.Button, onClick = onClick)
        .semantics { contentDescription = label }, contentAlignment = Alignment.Center) {
        PeilIcon(icon, Mg.peil.foreground, size = 24.dp)
    }
}
