package nl.mediaguard.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import nl.mediaguard.app.ui.theme.MgSpace

/** Standaard schermlijst: 16 dp zijmarge, 12 dp tussen kaarten, trekken om te verversen. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ScreenList(refreshing: Boolean = false, onRefresh: (() -> Unit)? = null, content: LazyListScope.() -> Unit) {
    val list = @Composable {
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = MgSpace.s4, end = MgSpace.s4, top = MgSpace.s2, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(MgSpace.s3),
            content = content,
        )
    }
    if (onRefresh == null) list()
    else PullToRefreshBox(isRefreshing = refreshing, onRefresh = onRefresh, modifier = Modifier.fillMaxSize()) { list() }
}

val <T> UiState<T>.refreshing: Boolean get() = (this as? UiState.Data)?.refreshing == true
val <T> UiState<T>.valueOrNull: T? get() = (this as? UiState.Data)?.value
