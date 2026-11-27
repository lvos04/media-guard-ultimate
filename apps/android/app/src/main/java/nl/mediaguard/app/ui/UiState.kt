package nl.mediaguard.app.ui

import nl.mediaguard.app.data.Freshness
import nl.mediaguard.app.data.Staleness

/** Eén toestand per scherm: laden, gegevens of een fout in gewone taal. */
sealed interface UiState<out T> {
    data object Loading : UiState<Nothing>
    data class Data<T>(
        val value: T,
        val refreshing: Boolean = false,
        val freshness: Freshness = Freshness(),
    ) : UiState<T>
    data class Error(val message: String) : UiState<Nothing>
}

/** Laatst bekende tijd van de bibliotheektelling of het rollende dagoverzicht. */
fun UiState.Data<*>.summaryStaleSince(now: Long): Long? =
    Staleness.staleSince(freshness, now, 2 * Staleness.SUMMARY_INTERVAL_MS)

fun <T> UiState<T>.currentValue(now: Long): T? =
    (this as? UiState.Data)?.takeIf {
        it.freshness.lastOkAt != null && Staleness.staleSince(it.freshness, now) == null
    }?.value
