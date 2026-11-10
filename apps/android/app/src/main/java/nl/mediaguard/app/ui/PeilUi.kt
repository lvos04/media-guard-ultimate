package nl.mediaguard.app.ui

import androidx.annotation.DrawableRes
import nl.mediaguard.app.R
import nl.mediaguard.app.data.AttentionItem

// Peil-betekenis zonder Compose (unit-getest): welke vorm, welk icoon en welk woord iets krijgt.
// docs/ontwerp/PEIL_MIGRATIE.md §6.2 en §10.2, ONTWERPTAAL §4 en §7, ADR-0018.

/**
 * Soort (kleur + vorm) van een melding, label of icoon. Kleur draagt nooit alleen de betekenis: er staat altijd
 * een icoon en een woord bij.
 * - [Ok] = Gezond: alleen gemeten gezondheid (nooit "klaar", "beschikbaar", "aan" of "live").
 * - [Warn] = Beperkt, [Bad] = Storing of gemeten falen.
 * - [Info] = informatie (blauw; nooit gezond). [Paused] = een bewuste pauze (ook de noodstop).
 * - [Unknown] = onbekend of niet beschikbaar (gestippeld), [Stale] = verouderd (gestippeld + arcering).
 * - [Muted] = neutraal label, [Strong] = neutraal-sterk: een voorwaarde (gate) of iets dat ontbreekt.
 */
enum class Tone { Ok, Warn, Bad, Info, Paused, Unknown, Stale, Muted, Strong;

    /** Gestippelde rand: wat niet (meer) gemeten is. */
    val dashed: Boolean get() = this == Unknown || this == Stale
}

/** Gezondheid en verwante toestanden (Peil §4.3): vast woord, vast icoon, pil met 2 dp-rand. */
enum class PeilStatus(@DrawableRes val icoon: Int, val woord: String, val tone: Tone) {
    Gezond(R.drawable.lucide_circle_check, "Gezond", Tone.Ok),
    Beperkt(R.drawable.lucide_triangle_alert, "Beperkt", Tone.Warn),
    Storing(R.drawable.lucide_octagon_x, "Storing", Tone.Bad),
    Onbekend(R.drawable.lucide_circle_help, "Onbekend", Tone.Unknown),
    Verouderd(R.drawable.lucide_clock_alert, "Verouderd", Tone.Stale),
    Gepauzeerd(R.drawable.lucide_circle_pause, "Gepauzeerd", Tone.Paused),
    NietGekoppeld(R.drawable.lucide_link_2_off, "Niet gekoppeld", Tone.Unknown),
    NietBeschikbaar(R.drawable.lucide_circle_slash, "Niet beschikbaar", Tone.Unknown),
}

/**
 * Een label met eigen icoon en woord. [soort] is [Tone.Muted] (neutraal) of [Tone.Strong] (neutraal-sterk); een
 * statussoort ([Tone.Bad] …) tekent hetzelfde label in de vorm van die status (pil, statuskleur).
 */
data class Kenmerk(val tekst: String, @DrawableRes val icoon: Int, val soort: Tone = Tone.Muted)

/**
 * Beschikbaarheid in de bibliotheek (zelfde woorden als de web). Beschikbaarheid is geen gezondheid
 * (ADR-0016 punt 6): Compleet, Kan beter, Verwacht en Niet gevolgd zijn neutraal, ontbreken is neutraal-sterk.
 */
object Beschikbaarheid {
    fun compleet() = Kenmerk("Compleet", R.drawable.lucide_check)
    fun ontbreekt(tekst: String) = Kenmerk(tekst, R.drawable.lucide_file_x, Tone.Strong)
    fun kanBeter() = Kenmerk("Kan beter", R.drawable.lucide_trending_up)
    fun verwacht() = Kenmerk("Verwacht", R.drawable.lucide_calendar_clock)
    fun nietGevolgd() = Kenmerk("Niet gevolgd", R.drawable.lucide_eye_off)
    fun doelBereikt(tekst: String = "Doel bereikt") = Kenmerk(tekst, R.drawable.lucide_check)
}

/** Soort punt in "Wat actie nodig heeft": icoon + kleur per soort, nooit een vinkje voor iets dat openstaat. */
enum class AttentionKind(@DrawableRes val icoon: Int, val tone: Tone, val woord: String) {
    Storing(R.drawable.lucide_octagon_x, Tone.Bad, "Storing"),
    Beperkt(R.drawable.lucide_triangle_alert, Tone.Warn, "Waarschuwing"),
    Voorwaarde(R.drawable.lucide_lock, Tone.Strong, "Wacht op een voorwaarde"),
    Info(R.drawable.lucide_info, Tone.Info, "Info");

    companion object {
        /** Sleutels uit de core die een voorwaarde zijn (gate), geen storing: vergrendeling, baan wacht op ruimte. */
        fun isGate(key: String): Boolean = key == "library.locked" || key.startsWith("encoder.paused.")

        fun of(severity: String?, key: String = ""): AttentionKind = when {
            isGate(key) -> Voorwaarde
            else -> when (severity?.lowercase()) {
                "error", "danger", "critical", "bad" -> Storing
                "warning", "warn" -> Beperkt
                else -> Info
            }
        }

        fun of(item: AttentionItem): AttentionKind = of(item.severity, item.key)
    }
}

/**
 * Totaalstatus voor het statuspaneel op Overzicht (zelfde regel als de web, ADR-0017 punt 4): een storing of
 * waarschuwing in de lijst → Beperkt; anders Gezond. Voorwaarden (gate) en info maken de status niet slechter.
 */
fun totalStatus(errors: Int, items: List<AttentionItem>): PeilStatus =
    if (errors > 0 || items.any { AttentionKind.of(it).let { k -> k == AttentionKind.Storing || k == AttentionKind.Beperkt } })
        PeilStatus.Beperkt else PeilStatus.Gezond

/** Baan GPU/CPU: icoon `microchip`/`cpu` in de baankleur, tekst in inkt (Peil §7, PEIL_MIGRATIE §10.2). */
enum class Baan(@DrawableRes val icoon: Int) {
    Gpu(R.drawable.lucide_microchip), Cpu(R.drawable.lucide_cpu), Anders(R.drawable.lucide_zap);

    companion object {
        fun of(lane: String): Baan = when (lane.lowercase()) {
            "gpu" -> Gpu
            "cpu" -> Cpu
            else -> Anders
        }
    }
}
