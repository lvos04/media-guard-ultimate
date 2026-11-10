package nl.mediaguard.app.ui

/** Eén toestand per scherm: laden, gegevens of een fout in gewone taal. */
sealed interface UiState<out T> {
    data object Loading : UiState<Nothing>
    data class Data<T>(val value: T, val refreshing: Boolean = false) : UiState<T>
    data class Error(val message: String) : UiState<Nothing>
}
