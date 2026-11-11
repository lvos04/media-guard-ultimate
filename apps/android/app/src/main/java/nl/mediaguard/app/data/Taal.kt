package nl.mediaguard.app.data

import java.util.Locale

/**
 * Taal van de app-teksten: Nederlands (de brontaal) of Engels (en-US). ADR-0023.
 *
 * De app volgt de taal die Android voor deze app kiest: de systeemtaal, of op Android 13+ de taal per app
 * (Instellingen → Apps → MediaGuard → Taal; `res/xml/locales_config.xml`). Engels geeft en-US; elke andere taal
 * geeft Nederlands, net zoals Android zelf op de standaardteksten in `res/values` terugvalt.
 */
enum class AppTaal(val tag: String) {
    Nl("nl-NL"), En("en-US");

    /** Voor getallen, maand- en dagnamen. */
    val locale: Locale get() = Locale.forLanguageTag(tag)

    companion object {
        /** De code uit `R.string.app_taal` ("nl" in `values`, "en" in `values-en`); onbekend = Nederlands. */
        fun of(code: String?): AppTaal = if (code?.trim()?.lowercase(Locale.ROOT)?.startsWith("en") == true) En else Nl
    }
}

/** De taal die nu geldt. Gezet bij de start van de app, van elk scherm en van elke achtergrondtaak. */
object Taal {
    @Volatile var huidig: AppTaal = AppTaal.Nl
}

/**
 * Eén tekst in beide talen, op de plek waar hij gebruikt wordt: `tr("Opnieuw proberen", "Try again")`.
 * Zo kan een tekst nooit in maar één taal bestaan; de compiler eist beide.
 */
fun tr(nl: String, en: String): String = if (Taal.huidig == AppTaal.En) en else nl
