package nl.mediaguard.app.ui

import androidx.annotation.DrawableRes
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import nl.mediaguard.app.R
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.ui.theme.Mg
import nl.mediaguard.app.ui.theme.MgShape
import nl.mediaguard.app.ui.theme.MgSpace
import nl.mediaguard.app.ui.theme.TouchTarget
import nl.mediaguard.app.ui.theme.cijfers

// Bouwstenen in Peil 1.1.0 (docs/ontwerp/PEIL_MIGRATIE.md §10, ADR-0018): vlakke kaarten met een 1 dp-rand,
// status als icoon + vast woord + vorm, neutrale labels, knoppen en tabs van minstens 48 dp, stippelranden en
// arcering in plaats van doorzichtigheid, geen tekst onder 14 sp en geen ellips op inhoud.

// ---------------------------------------------------------------------------------- kleuren per soort

/** Kleur van het icoon (en de rand) van een soort. */
@Composable
fun Tone.fg(): Color {
    val p = Mg.peil
    return when (this) {
        Tone.Ok -> p.healthy
        Tone.Warn -> p.degraded
        Tone.Bad -> p.down
        Tone.Info, Tone.Paused -> p.info
        Tone.Unknown -> p.unknown
        Tone.Stale -> p.stale
        Tone.Muted -> p.mutedForeground
        Tone.Strong -> p.gate
    }
}

/** Tekstkleur op het eigen vlak (≥ 7:1). */
@Composable
fun Tone.ink(): Color {
    val p = Mg.peil
    return when (this) {
        Tone.Ok -> p.healthyInk
        Tone.Warn -> p.degradedInk
        Tone.Bad -> p.downInk
        Tone.Info, Tone.Paused -> p.infoInk
        Tone.Unknown, Tone.Stale -> p.unknownInk
        Tone.Muted -> p.foreground
        Tone.Strong -> p.gateInk
    }
}

/** Vlak van een melding of label. Een tint draagt nooit alleen betekenis (Peil §4.1 regel 9). */
@Composable
fun Tone.soft(): Color {
    val p = Mg.peil
    return when (this) {
        Tone.Ok -> p.healthyBg
        Tone.Warn -> p.degradedBg
        Tone.Bad -> p.downBg
        Tone.Info, Tone.Paused -> p.infoBg
        Tone.Unknown -> p.unknownBg
        Tone.Stale -> p.staleBg
        Tone.Muted -> p.muted
        Tone.Strong -> p.gateBg
    }
}

/** Rand van een melding: de statuskleur, sterk-neutraal of de sterke rand. */
@Composable
fun Tone.edge(): Color = when (this) {
    Tone.Muted -> Mg.peil.borderStrong
    else -> fg()
}

// ---------------------------------------------------------------------------------- tekenhulpen

/** Een lucide-icoon (res/drawable/lucide_*.xml) in [tint]; decoratief, de tekst ernaast zegt wat het betekent. */
@Composable
fun PeilIcon(@DrawableRes id: Int, tint: Color, modifier: Modifier = Modifier, size: Dp = 20.dp) {
    Icon(painterResource(id), contentDescription = null, tint = tint, modifier = modifier.size(size))
}

/** Gestippelde rand (Onbekend, Verouderd, uitgeschakeld): over de inhoud, binnen de randen getekend. */
fun Modifier.stippelRand(color: Color, width: Dp = 2.dp, radius: Dp = 8.dp, pill: Boolean = false): Modifier =
    drawWithContent {
        drawContent()
        val w = width.toPx()
        val r = if (pill) (size.height - w) / 2 else radius.toPx()
        drawRoundRect(
            color = color,
            topLeft = Offset(w / 2, w / 2),
            size = Size(size.width - w, size.height - w),
            cornerRadius = CornerRadius(r, r),
            style = Stroke(w, pathEffect = PathEffect.dashPathEffect(floatArrayOf(3 * w, 2 * w))),
        )
    }

/** Arcering onder 45° achter de inhoud (verouderde waarde, "niet verklaard"); clip eerst de vorm. */
fun Modifier.arcering(color: Color, spacing: Dp = 6.dp, width: Dp = 1.5.dp): Modifier =
    drawBehind { arceer(color, spacing.toPx(), width.toPx()) }

fun DrawScope.arceer(color: Color, spacing: Float, width: Float) {
    clipRect(0f, 0f, size.width, size.height) {
        var x = -size.height
        while (x < size.width) {
            drawLine(color, start = Offset(x, size.height), end = Offset(x + size.height, 0f), strokeWidth = width)
            x += spacing
        }
    }
}

/** Rand in een soort: 2 dp, gestippeld voor Onbekend/Verouderd. */
fun Modifier.soortRand(tone: Tone, color: Color, shape: Shape, radius: Dp = 8.dp, pill: Boolean = false): Modifier =
    if (tone.dashed) stippelRand(color, radius = radius, pill = pill) else border(BorderStroke(2.dp, color), shape)

// ---------------------------------------------------------------------------------- kaart

/**
 * Kaart (Peil Card): kaartvlak, 1 dp-rand, hoeken 8 dp, geen schaduw. Met [tone] wordt de rand 2 dp in die soort
 * (gestippeld voor Onbekend/Verouderd), bijvoorbeeld het statuspaneel of een verouderde kaart.
 */
@Composable
fun MgCard(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    padding: PaddingValues = PaddingValues(MgSpace.s4),
    border: Color = Mg.colors.line,
    background: Color = Mg.colors.surface,
    tone: Tone? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val shape = MgShape.card
    val rand = if (tone != null) Modifier.soortRand(tone, tone.edge(), shape) else Modifier.border(1.dp, border, shape)
    Column(
        modifier.fillMaxWidth().clip(shape).background(background)
            .then(rand)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(padding),
        verticalArrangement = Arrangement.spacedBy(MgSpace.s3),
        content = content,
    )
}

/** Kop van een kaart: icoon, titel (kop voor TalkBack), optioneel een teller en rechts een actie. */
@Composable
fun CardHeader(
    title: String,
    @DrawableRes icon: Int? = null,
    count: Int? = null,
    countLabel: String? = null,
    trailing: (@Composable RowScope.() -> Unit)? = null,
) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
            if (icon != null) {
                PeilIcon(icon, Mg.colors.muted)
                Spacer(Modifier.width(MgSpace.s2))
            }
            Text(title, style = Mg.type.h2, color = Mg.colors.text,
                modifier = Modifier.weight(1f, fill = false).semantics { heading() })
            if (count != null) {
                Spacer(Modifier.width(MgSpace.s2))
                CountBadge(count, countLabel)
            }
        }
        if (trailing != null) { Spacer(Modifier.width(MgSpace.s2)); trailing() }
    }
}

/** Teller (Peil: inktpil, nooit rood). [label] is de toegankelijke naam ("3 dingen wachten op jou"). */
@Composable
fun CountBadge(count: Int, label: String? = null) {
    Box(
        Modifier.heightIn(min = 24.dp).clip(MgShape.pill).background(Mg.peil.muted)
            .border(1.dp, Mg.peil.borderStrong, MgShape.pill).padding(horizontal = 8.dp)
            .then(if (label != null) Modifier.clearAndSetSemantics { contentDescription = label } else Modifier),
        contentAlignment = Alignment.Center,
    ) {
        Text("$count", style = Mg.type.meta.copy(fontWeight = FontWeight.SemiBold).cijfers, color = Mg.colors.text)
    }
}

/** Paginakop: titel (één per scherm, kop voor TalkBack) + omschrijving. */
@Composable
fun PageHeader(title: String, sub: String? = null, trailing: (@Composable RowScope.() -> Unit)? = null) {
    Row(Modifier.fillMaxWidth().padding(top = MgSpace.s2), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = Mg.type.title, color = Mg.colors.text, modifier = Modifier.semantics { heading() })
            if (!sub.isNullOrBlank()) Text(sub, style = Mg.type.small, color = Mg.colors.muted)
        }
        trailing?.invoke(this)
    }
}

/** Tussenkop in een lijst: 14 sp vet, geen kapitalen (Peil §5). */
@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(text, style = Mg.type.meta.copy(fontWeight = FontWeight.SemiBold), color = Mg.colors.muted,
        modifier = modifier.semantics { heading() })
}

// ---------------------------------------------------------------------------------- status en labels

/**
 * StatusBadge (Peil C-03): icoon 18 dp + vast woord in een pil met een 2 dp-rand (gestippeld voor Onbekend en
 * Verouderd, gearceerd voor Verouderd; Gezond zonder vlak en zonder rand). [label] vervangt het woord alleen waar
 * het scherm hetzelfde in eigen woorden zegt ("Noodstop actief", "Alles gezond"). [reason] staat als tekst
 * ernaast en hoort bij de toegankelijke naam. De tekst loopt om; nooit een ellips.
 */
@Composable
fun StatusBadge(status: PeilStatus, modifier: Modifier = Modifier, label: String? = null, reason: String? = null,
                large: Boolean = false) {
    val tone = status.tone
    val woord = label ?: status.woord
    val shape = MgShape.pill
    val spoken = listOfNotNull(woord, reason?.ifBlank { null }).joinToString(", ")
    Row(
        modifier.clearAndSetSemantics { contentDescription = spoken },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(MgSpace.s2),
    ) {
        Row(
            Modifier.heightIn(min = if (large) 40.dp else 32.dp)
                .clip(shape)
                .then(if (tone == Tone.Ok) Modifier else Modifier.background(tone.soft()))
                .then(if (tone == Tone.Stale) Modifier.arcering(Mg.peil.staleHatch) else Modifier)
                .then(if (tone == Tone.Ok) Modifier else Modifier.soortRand(tone, tone.fg(), shape, pill = true))
                .padding(horizontal = if (tone == Tone.Ok) 0.dp else 10.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            PeilIcon(status.icoon, tone.fg(), size = if (large) 20.dp else 18.dp)
            Text(woord, style = (if (large) Mg.type.body else Mg.type.meta).copy(fontWeight = FontWeight.SemiBold),
                color = tone.ink())
        }
        if (!reason.isNullOrBlank()) Text(reason, style = Mg.type.meta, color = Mg.colors.muted,
            modifier = Modifier.weight(1f, fill = false))
    }
}

/**
 * Label (Peil: neutraal of neutraal-sterk): icoon 16 dp + woord in een rechthoek met hoeken van 6 dp — status is
 * rond, een label hoekig. Neutraal: kaartvlak met 1 dp `border-strong` (een baankleur als icoon haalt daarop
 * ≥ 3:1); sterk (voorwaarde, ontbreekt): `gate-bg` met 2 dp inktrand. Een
 * statussoort tekent het als StatusBadge-vorm met eigen woord. [iconTint] kleurt alleen het icoon (baankleur);
 * de tekst blijft inkt.
 */
@Composable
fun PeilLabel(k: Kenmerk, modifier: Modifier = Modifier, iconTint: Color? = null, background: Color? = null) {
    val tone = k.soort
    if (tone != Tone.Muted && tone != Tone.Strong) {
        val status = PeilStatus.entries.firstOrNull { it.tone == tone && it.icoon == k.icoon }
            ?: PeilStatus.entries.firstOrNull { it.tone == tone }
        if (status != null) {
            StatusBadge(status, modifier, label = k.tekst)
            return
        }
    }
    val shape = RoundedCornerShape(6.dp)
    Row(
        modifier.heightIn(min = 28.dp).clip(shape).background(background ?: if (tone == Tone.Muted) Mg.peil.card else tone.soft())
            .then(when (tone) {
                Tone.Muted -> Modifier.border(1.dp, Mg.peil.borderStrong, shape)
                else -> Modifier.border(2.dp, tone.edge(), shape)
            })
            .padding(horizontal = 8.dp, vertical = 3.dp)
            .semantics(mergeDescendants = true) {},
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        PeilIcon(k.icoon, iconTint ?: tone.fg(), size = 16.dp)
        Text(k.tekst, style = Mg.type.meta.copy(fontWeight = FontWeight.Medium), color = tone.ink())
    }
}

/** Labels naast elkaar die omlopen (nooit buiten beeld). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun LabelRow(labels: List<Kenmerk>, modifier: Modifier = Modifier) {
    if (labels.isEmpty()) return
    FlowRow(modifier, horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        labels.forEach { PeilLabel(it) }
    }
}

// ---------------------------------------------------------------------------------- KPI en rijen

/**
 * KPI-tegel in de stijl van StatKaart (Peil C-05): titel met icoon, waarde 30 sp in inkt (nooit in statuskleur),
 * eventueel een statusbadge, toelichting. [staleSince] = verouderd: gestippelde rand, "Laatst bekend · 12:06" boven
 * de waarde, waarde in `stale-ink` op een gearceerd vak. Zonder [value] (onbekend) staat er geen getal, alleen de
 * status Onbekend met reden.
 */
@Composable
fun KpiTile(
    label: String,
    value: String?,
    modifier: Modifier = Modifier,
    sub: String? = null,
    @DrawableRes icon: Int? = null,
    status: PeilStatus? = null,
    statusLabel: String? = null,
    staleSince: String? = null,
    onClick: (() -> Unit)? = null,
) {
    val stale = staleSince != null
    MgCard(modifier, onClick = onClick, padding = PaddingValues(14.dp), tone = if (stale) Tone.Stale else null) {
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (icon != null) {
                    PeilIcon(icon, Mg.colors.muted, size = 18.dp)
                    Spacer(Modifier.width(6.dp))
                }
                Text(label, style = Mg.type.meta.copy(fontWeight = FontWeight.SemiBold), color = Mg.colors.text,
                    modifier = Modifier.semantics { heading() })
            }
            if (status != null) StatusBadge(status, label = statusLabel)
            when {
                value == null -> if (status == null) StatusBadge(PeilStatus.Onbekend)
                stale -> Column(
                    Modifier.clip(RoundedCornerShape(6.dp)).background(Mg.peil.staleBg).arcering(Mg.peil.staleHatch)
                        .stippelRand(Mg.peil.stale, width = 1.5.dp, radius = 6.dp).padding(horizontal = 8.dp, vertical = 4.dp)
                        .clearAndSetSemantics { contentDescription = tr("Laatst bekend · $staleSince: $value (laatst bekende waarde, verouderd)", "Last known · $staleSince: $value (last known value, stale)") },
                ) {
                    Text(tr("Laatst bekend · $staleSince", "Last known · $staleSince"), style = Mg.type.meta, color = Mg.peil.staleInk)
                    Text(value, style = Mg.type.kpi.copy(fontWeight = FontWeight.Normal), color = Mg.peil.staleInk)
                }
                else -> Text(value, style = Mg.type.kpi, color = Mg.colors.text)
            }
            if (!sub.isNullOrBlank()) Text(sub, style = Mg.type.meta, color = Mg.colors.muted)
        }
    }
}

/** Twee tegels naast elkaar, even hoog (KPI-raster 2×2 op de telefoon). */
@Composable
fun TileRow(content: @Composable RowScope.() -> Unit) {
    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Max), horizontalArrangement = Arrangement.spacedBy(MgSpace.s3),
        content = content)
}

/** Lijstregel: optioneel iets links, titel + subregel (lopen om), iets rechts; aanraakbaar = minstens 48 dp. */
@Composable
fun ListRow(
    title: String,
    modifier: Modifier = Modifier,
    sub: String? = null,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
    trailingText: String? = null,
    divider: Boolean = false,
    onClick: (() -> Unit)? = null,
) {
    Column(modifier.fillMaxWidth()) {
        if (divider) Box(Modifier.fillMaxWidth().height(1.dp).background(Mg.colors.lineSoft))
        Row(
            Modifier.fillMaxWidth().heightIn(min = TouchTarget)
                .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
                .padding(vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (leading != null) { leading(); Spacer(Modifier.width(MgSpace.s3)) }
            Column(Modifier.weight(1f)) {
                Text(title, style = Mg.type.body.copy(fontWeight = FontWeight.Medium), color = Mg.colors.text)
                if (!sub.isNullOrBlank()) Text(sub, style = Mg.type.small, color = Mg.colors.muted)
            }
            if (trailingText != null) {
                Spacer(Modifier.width(MgSpace.s2))
                Text(trailingText, style = Mg.type.small.copy(fontWeight = FontWeight.SemiBold).cijfers, color = Mg.colors.text)
            }
            if (trailing != null) { Spacer(Modifier.width(MgSpace.s2)); trailing() }
        }
    }
}

/**
 * Sleutel-waarde-regel (feit): sleutel in de tweede tekstkleur, waarde in inkt; lange waarden lopen om.
 * De sleutel krijgt een kolom van 120 dp. Vult de sleutel die kolom bijna helemaal (grote letter: "In MediaGuard",
 * "Latest version"), dan schuift de waarde op tot er minstens [MgSpace.s2] tussen zit, zodat de twee nooit aan elkaar
 * plakken ("In MediaGuardoff"). Past de sleutel ruim, dan staat alles op dezelfde plek als voorheen.
 */
@Composable
fun KeyValue(key: String, value: String) {
    Layout(
        content = {
            Text(key, style = Mg.type.small, color = Mg.colors.muted)
            Text(value, style = Mg.type.small, color = Mg.colors.text)
        },
        modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
    ) { measurables, constraints ->
        val column = 120.dp.roundToPx()
        val keyPlaceable = measurables[0].measure(Constraints(maxWidth = column))
        val start = maxOf(column, keyPlaceable.width + MgSpace.s2.roundToPx())
        val valuePlaceable = measurables[1].measure(Constraints(maxWidth = (constraints.maxWidth - start).coerceAtLeast(0)))
        layout(constraints.maxWidth, maxOf(keyPlaceable.height, valuePlaceable.height)) {
            keyPlaceable.place(0, 0)
            valuePlaceable.place(start, 0)
        }
    }
}

/** Icoonhouder links in een lijstregel: icoon in de soort op het vlak van die soort. */
@Composable
fun IconBubble(@DrawableRes icon: Int, tone: Tone = Tone.Muted, size: Dp = 36.dp) {
    Box(Modifier.size(size).clip(RoundedCornerShape(8.dp)).background(tone.soft()), contentAlignment = Alignment.Center) {
        PeilIcon(icon, tone.fg(), size = 20.dp)
    }
}

// ---------------------------------------------------------------------------------- voortgang

/**
 * Voortgangsbalk (Peil Progress): de kleur is een grafiek- of baankleur (nooit een statuskleur). Het spoor is het
 * kaartvlak met een 1 dp-rand `border-strong`: zo haalt elke reeks- en baankleur ≥ 3:1 tegen het spoor (op
 * `chart-meter-track` haalt de CPU-baan maar 2,4:1; PeilContrastTest). Zonder [fraction] alleen het spoor.
 */
@Composable
fun MgProgress(fraction: Float?, modifier: Modifier = Modifier, color: Color = Mg.peil.chart1, height: Dp = 8.dp,
               description: String? = null) {
    val f by animateFloatAsState((fraction ?: 0f).coerceIn(0f, 1f), tween(150), label = "progress")
    Box(
        modifier.fillMaxWidth().height(height).clip(MgShape.pill).background(Mg.peil.card)
            .border(1.dp, Mg.peil.borderStrong, MgShape.pill)
            .semantics {
                if (fraction != null) progressBarRangeInfo = ProgressBarRangeInfo(fraction.coerceIn(0f, 1f), 0f..1f)
                if (description != null) stateDescription = description
            },
    ) {
        if (fraction != null) Box(Modifier.fillMaxHeight().fillMaxWidth(f).clip(MgShape.pill).background(color))
    }
}

// ---------------------------------------------------------------------------------- bediening

/** Peil-knopsoorten: primair, secundair, stil, gevaar (rand) en gevaar-vol. */
enum class ButtonKind { Primary, Secondary, Danger, DangerSolid, Ghost }

/**
 * Knop (Peil Button): minstens 48 dp hoog, hoeken 8 dp, 2 dp-rand, label 16 sp vet (klein: 14 sp). Uitgeschakeld =
 * gestippelde rand op het `muted`-vlak met `disabled-foreground`, nooit doorzichtigheid. Een knop met alleen een
 * icoon heeft een [contentDescription]. De tekst loopt om.
 */
@Composable
fun MgButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    kind: ButtonKind = ButtonKind.Secondary,
    @DrawableRes icon: Int? = null,
    enabled: Boolean = true,
    small: Boolean = false,
    contentDescription: String? = null,
) {
    val p = Mg.peil
    val (bg, fg, border) = when (kind) {
        ButtonKind.Primary -> Triple(p.primary, p.primaryForeground, p.primary)
        ButtonKind.Secondary -> Triple(p.card, p.foreground, p.borderStrong)
        ButtonKind.Danger -> Triple(p.card, p.destructive, p.destructive)
        ButtonKind.DangerSolid -> Triple(p.destructive, p.destructiveForeground, p.destructive)
        ButtonKind.Ghost -> Triple(Color.Transparent, p.foreground, Color.Transparent)
    }
    val shape = MgShape.control
    val iconOnly = text.isEmpty() && icon != null
    Row(
        modifier.heightIn(min = TouchTarget).widthIn(min = TouchTarget)
            .clip(shape)
            .background(if (enabled) bg else p.muted)
            .then(if (enabled) Modifier.border(2.dp, border, shape) else Modifier.stippelRand(p.borderStrong, width = 1.5.dp))
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .then(if (contentDescription != null) Modifier.semantics { this.contentDescription = contentDescription } else Modifier)
            .padding(horizontal = if (iconOnly) 0.dp else if (small) 12.dp else 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        val color = if (enabled) fg else p.disabledForeground
        if (icon != null) {
            PeilIcon(icon, color, size = 20.dp)
            if (!iconOnly) Spacer(Modifier.width(8.dp))
        }
        if (!iconOnly) Text(text, style = (if (small) Mg.type.meta else Mg.type.body).copy(fontWeight = FontWeight.SemiBold),
            color = color, textAlign = TextAlign.Center)
    }
}

/**
 * Keuze uit een paar opties (Peil ToggleGroup): segmenten van minstens 48 dp; gekozen = inkt-vlak met
 * achtergrondkleur-tekst en een vinkje (niet alleen kleur).
 */
@Composable
fun Segmented(options: List<String>, selected: Int, onSelect: (Int) -> Unit, modifier: Modifier = Modifier) {
    val p = Mg.peil
    Row(
        modifier.fillMaxWidth().height(IntrinsicSize.Max).clip(MgShape.segment).background(p.card)
            .border(1.dp, p.borderStrong, MgShape.segment).padding(3.dp),
        horizontalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        options.forEachIndexed { i, label ->
            val on = i == selected
            Row(
                Modifier.weight(1f).fillMaxHeight().heightIn(min = 42.dp).clip(RoundedCornerShape(6.dp))
                    .background(if (on) p.foreground else Color.Transparent)
                    .selectable(on, role = Role.Tab) { onSelect(i) }
                    .padding(horizontal = 8.dp, vertical = 6.dp),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (on) { PeilIcon(R.drawable.lucide_check, p.background, size = 18.dp); Spacer(Modifier.width(6.dp)) }
                Text(label, style = Mg.type.meta.copy(fontWeight = FontWeight.SemiBold),
                    color = if (on) p.background else p.foreground, textAlign = TextAlign.Center)
            }
        }
    }
}

/**
 * Schakelaar (Peil Switch): 52 × 32 dp binnen een aanraakvlak van 48 dp. Aan = primair spoor met een vinkje in de
 * knop; uit = sterke rand. Uitgeschakeld = gestippelde rand op `muted`, nooit doorzichtigheid.
 */
@Composable
fun MgSwitch(checked: Boolean, onChange: (Boolean) -> Unit, enabled: Boolean = true, modifier: Modifier = Modifier) {
    val pos by animateFloatAsState(if (checked) 1f else 0f, tween(150), label = "switch")
    val p = Mg.peil
    Box(
        modifier.size(width = 56.dp, height = TouchTarget)
            .toggleable(checked, enabled = enabled, role = Role.Switch, onValueChange = onChange),
        contentAlignment = Alignment.Center,
    ) {
        val track = when {
            !enabled -> p.muted
            checked -> p.primary
            else -> p.card
        }
        Box(
            Modifier.size(width = 52.dp, height = 32.dp).clip(MgShape.pill).background(track)
                .then(when {
                    !enabled -> Modifier.stippelRand(p.borderStrong, width = 1.5.dp, pill = true)
                    checked -> Modifier
                    else -> Modifier.border(2.dp, p.borderStrong, MgShape.pill)
                })
                .padding(4.dp),
        ) {
            Box(
                Modifier.offset(x = 20.dp * pos).size(24.dp).clip(CircleShape)
                    .background(when {
                        !enabled -> p.disabledForeground
                        checked -> p.primaryForeground
                        else -> p.mutedForeground
                    }),
                contentAlignment = Alignment.Center,
            ) {
                if (checked) PeilIcon(R.drawable.lucide_check, if (enabled) p.primary else p.muted, size = 16.dp)
            }
        }
    }
}

/**
 * Tabs als omlopende knoppen (Peil Tabs < 768): minstens 48 dp, sterke rand; gekozen = inkt-vlak met
 * achtergrondkleur-tekst en een vinkje. Lopen om, dus de gekozen tab valt nooit buiten beeld.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun <K> FilterChips(options: List<Pair<K, String>>, selected: K, onSelect: (K) -> Unit, modifier: Modifier = Modifier,
                    counts: Map<K, Int> = emptyMap()) {
    val p = Mg.peil
    FlowRow(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(MgSpace.s2),
        verticalArrangement = Arrangement.spacedBy(MgSpace.s2)) {
        options.forEach { (key, label) ->
            val on = key == selected
            val count = counts[key]?.takeIf { it > 0 }
            Row(
                Modifier.heightIn(min = TouchTarget).clip(MgShape.control)
                    .background(if (on) p.foreground else p.card)
                    .border(1.dp, if (on) p.foreground else p.borderStrong, MgShape.control)
                    .selectable(on, role = Role.Tab) { onSelect(key) }
                    .padding(horizontal = 14.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (on) { PeilIcon(R.drawable.lucide_check, p.background, size = 18.dp); Spacer(Modifier.width(6.dp)) }
                Text(label, style = Mg.type.meta.copy(fontWeight = FontWeight.SemiBold), color = if (on) p.background else p.foreground)
                if (count != null) {
                    Spacer(Modifier.width(6.dp))
                    Box(Modifier.clip(MgShape.pill).background(if (on) p.background else p.muted).padding(horizontal = 7.dp)) {
                        Text("$count", style = Mg.type.meta.copy(fontWeight = FontWeight.SemiBold).cijfers, color = p.foreground)
                    }
                }
            }
        }
    }
}

/** Zoekveld: kaartvlak, rand `input`, vergrootglas, tekst 16 sp, minstens 48 dp hoog. */
@Composable
fun MgSearchField(value: String, onChange: (String) -> Unit, placeholder: String, onSearch: () -> Unit,
                  modifier: Modifier = Modifier) {
    val p = Mg.peil
    Row(
        modifier.fillMaxWidth().heightIn(min = TouchTarget).clip(MgShape.control).background(p.card)
            .border(1.dp, p.input, MgShape.control).padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PeilIcon(R.drawable.lucide_search, p.mutedForeground)
        Spacer(Modifier.width(MgSpace.s2))
        Box(Modifier.weight(1f).padding(vertical = 12.dp)) {
            if (value.isEmpty()) Text(placeholder, style = Mg.type.body, color = p.mutedForeground)
            BasicTextField(
                value, onChange, singleLine = true, textStyle = Mg.type.body.copy(color = p.foreground),
                cursorBrush = SolidColor(p.primary),
                modifier = Modifier.fillMaxWidth().semantics { contentDescription = placeholder },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { onSearch() }),
            )
        }
    }
}

// ---------------------------------------------------------------------------------- tijdlijn

data class TimelineEntry(val title: String, val sub: String? = null, val time: String? = null)

/** Tijdlijn: neutrale punten (een gebeurtenis is geen status) met een lijn ertussen; tijd in vaste cijfers. */
@Composable
fun Timeline(entries: List<TimelineEntry>) {
    val p = Mg.peil
    Column {
        entries.forEachIndexed { i, e ->
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                Box(Modifier.width(20.dp).fillMaxHeight()) {
                    if (i < entries.lastIndex) Box(Modifier.padding(start = 9.dp, top = 14.dp).width(2.dp).fillMaxHeight()
                        .background(p.border))
                    Box(Modifier.padding(start = 4.dp, top = 6.dp).size(12.dp).clip(CircleShape).background(p.card)
                        .border(2.dp, p.borderStrong, CircleShape))
                }
                Spacer(Modifier.width(MgSpace.s2))
                Column(Modifier.weight(1f).padding(bottom = if (i < entries.lastIndex) 14.dp else 0.dp)) {
                    Row(verticalAlignment = Alignment.Top) {
                        Text(e.title, style = Mg.type.body.copy(fontWeight = FontWeight.Medium), color = Mg.colors.text,
                            modifier = Modifier.weight(1f))
                        if (!e.time.isNullOrBlank()) {
                            Spacer(Modifier.width(MgSpace.s2))
                            Text(e.time, style = Mg.type.meta.cijfers, color = Mg.colors.muted)
                        }
                    }
                    if (!e.sub.isNullOrBlank()) Text(e.sub, style = Mg.type.meta, color = Mg.colors.muted)
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------------- statuspaneel en toestanden

/**
 * Statuspaneel bovenaan Overzicht (Peil SysteemStatusPaneel): titel + uitleg, de totaalstatus als StatusBadge
 * (groot) en één werkwoordknop. De rand volgt de status (Gezond = gewone kaartrand; Verouderd gestippeld).
 */
@Composable
fun StatusPanel(
    title: String,
    sub: String?,
    status: PeilStatus,
    statusLabel: String? = null,
    statusReason: String? = null,
    @DrawableRes icon: Int = status.icoon,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    val tone = status.tone
    MgCard(tone = if (tone == Tone.Ok) null else tone) {
        Row(verticalAlignment = Alignment.Top) {
            PeilIcon(icon, if (tone == Tone.Ok) Mg.colors.muted else tone.fg(), Modifier.padding(top = 2.dp), size = 24.dp)
            Spacer(Modifier.width(MgSpace.s3))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(title, style = Mg.type.h2, color = Mg.colors.text, modifier = Modifier.semantics { heading() })
                if (!sub.isNullOrBlank()) Text(sub, style = Mg.type.small, color = Mg.colors.muted)
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.semantics(mergeDescendants = true) {}) {
            Text(tr("Totaalstatus", "Overall status"), style = Mg.type.meta, color = Mg.colors.muted)
            Spacer(Modifier.width(MgSpace.s2))
            StatusBadge(status, label = statusLabel, reason = statusReason, large = true)
        }
        if (actionLabel != null && onAction != null) {
            MgButton(actionLabel, onAction, kind = ButtonKind.Primary, modifier = Modifier.fillMaxWidth())
        }
    }
}

/** Lege staat: icoon in een vakje, titel en uitleg, optioneel een actie. */
@Composable
fun EmptyState(title: String, sub: String? = null, @DrawableRes icon: Int = R.drawable.lucide_inbox,
               actionLabel: String? = null, onAction: (() -> Unit)? = null) {
    Column(Modifier.fillMaxWidth().padding(vertical = MgSpace.s3), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(MgSpace.s2)) {
        Box(Modifier.size(40.dp).clip(RoundedCornerShape(8.dp)).background(Mg.peil.muted)
            .border(1.dp, Mg.colors.line, RoundedCornerShape(8.dp)), contentAlignment = Alignment.Center) {
            PeilIcon(icon, Mg.colors.muted)
        }
        Text(title, style = Mg.type.bodyStrong, color = Mg.colors.text, textAlign = TextAlign.Center)
        if (!sub.isNullOrBlank()) Text(sub, style = Mg.type.small, color = Mg.colors.muted, textAlign = TextAlign.Center)
        if (actionLabel != null && onAction != null) MgButton(actionLabel, onAction, small = true)
    }
}

/** Laden: skeletkaarten in de vorm van de inhoud (geen draaiend wieltje); TalkBack hoort "Laden…". */
@Composable
fun LoadingState(cards: Int = 3) {
    Column(Modifier.fillMaxWidth().semantics(mergeDescendants = true) { contentDescription = tr("Laden…", "Loading…") },
        verticalArrangement = Arrangement.spacedBy(MgSpace.s3)) {
        repeat(cards) { i ->
            MgCard {
                SkeletonLine(0.4f, 16.dp)
                SkeletonLine(if (i % 2 == 0) 0.9f else 0.75f)
                SkeletonLine(0.6f)
            }
        }
    }
}

@Composable
fun SkeletonLine(width: Float, height: Dp = 12.dp) {
    Box(Modifier.fillMaxWidth(width).height(height).clip(RoundedCornerShape(4.dp)).background(Mg.peil.muted))
}

/**
 * Fout (Peil Foutpaneel, `melding-fout`): wat er mislukte, het waarom in gewone taal en "Opnieuw proberen". De app
 * krijgt geen code of trace-ID mee in zijn foutmodel; die staat dus niet in beeld (geen verzonnen waarden).
 */
@Composable
fun ErrorState(message: String, onRetry: () -> Unit) {
    Melding(Tone.Bad, R.drawable.lucide_octagon_x, tr("Laden is mislukt", "Loading failed"), message, action = {
        MgButton(tr("Opnieuw proberen", "Try again"), onRetry, small = true, icon = R.drawable.lucide_rotate_ccw)
    })
}

/**
 * Melding over de volle breedte (Peil `melding-*`): icoon + kop + uitleg op het vlak van de soort, 2 dp-rand
 * (gestippeld voor Onbekend). Een actie staat onder de tekst (telefoon), nooit ernaast geperst.
 */
@Composable
fun Melding(
    tone: Tone,
    @DrawableRes icon: Int,
    text: String,
    sub: String? = null,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)? = null,
) {
    val shape = MgShape.card
    Column(
        modifier.fillMaxWidth().clip(shape).background(tone.soft())
            .then(if (tone == Tone.Stale) Modifier.arcering(Mg.peil.staleHatch) else Modifier)
            .soortRand(tone, tone.edge(), shape)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(MgSpace.s2),
    ) {
        Row(verticalAlignment = Alignment.Top, modifier = Modifier.semantics(mergeDescendants = true) {}) {
            PeilIcon(icon, tone.fg(), Modifier.padding(top = 2.dp))
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(text, style = Mg.type.body.copy(fontWeight = FontWeight.SemiBold), color = tone.ink())
                if (!sub.isNullOrBlank()) Text(sub, style = Mg.type.small, color = tone.ink())
            }
        }
        if (action != null) Row(Modifier.padding(start = 30.dp)) { action() }
    }
}

/** Oude naam van [Melding]; zelfde volgorde van argumenten als vóór Peil. */
@Composable
fun Banner(text: String, tone: Tone, @DrawableRes icon: Int, sub: String? = null, action: (@Composable () -> Unit)? = null) =
    Melding(tone, icon, text, sub, action = action)

// ---------------------------------------------------------------------------------- bevestigen

/**
 * Bevestigingsvenster (Peil AlertDialog, hoeken 12 dp): knoplabels noemen de actie (nooit "OK"). Optioneel een
 * tweede uitkomst, bv. "Volgen en meteen zoeken" (primair) of "Alleen volgen". Gevaar = gevaar-vol.
 */
@Composable
fun ConfirmDialog(
    title: String,
    text: String,
    confirmLabel: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    danger: Boolean = false,
    secondaryLabel: String? = null,
    onSecondary: (() -> Unit)? = null,
    extra: (@Composable ColumnScope.() -> Unit)? = null,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Mg.peil.popover,
        shape = MgShape.sheet,
        title = { Text(title, style = Mg.type.section, color = Mg.colors.text) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(MgSpace.s3)) {
                Text(text, style = Mg.type.body, color = Mg.colors.text)
                extra?.invoke(this)
            }
        },
        confirmButton = {
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(MgSpace.s2)) {
                MgButton(confirmLabel, onConfirm, kind = if (danger) ButtonKind.DangerSolid else ButtonKind.Primary,
                    modifier = Modifier.fillMaxWidth())
                if (secondaryLabel != null && onSecondary != null)
                    MgButton(secondaryLabel, onSecondary, modifier = Modifier.fillMaxWidth())
                MgButton(tr("Annuleren", "Cancel"), onDismiss, kind = ButtonKind.Ghost, modifier = Modifier.fillMaxWidth())
            }
        },
    )
}

/** Schermvulling met de achtergrondkleur. */
@Composable
fun ScreenBox(content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize().background(Mg.colors.bg)) { content() }
}
