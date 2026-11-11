package nl.mediaguard.app

import android.Manifest
import android.app.DatePickerDialog
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import nl.mediaguard.app.data.Repository
import androidx.compose.runtime.key
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.launch
import nl.mediaguard.app.data.AttentionAction
import nl.mediaguard.app.data.Staleness
import nl.mediaguard.app.data.tr
import nl.mediaguard.app.ui.RecentViewModel
import nl.mediaguard.app.ui.screens.OverviewExtras
import nl.mediaguard.app.ui.screens.PairingReason
import nl.mediaguard.app.data.initials
import nl.mediaguard.app.ui.ConfirmDialog
import nl.mediaguard.app.ui.HistoryViewModel
import nl.mediaguard.app.ui.LibraryItemViewModel
import nl.mediaguard.app.ui.LibrarySearchViewModel
import nl.mediaguard.app.ui.MyRequestsViewModel
import nl.mediaguard.app.ui.SeerrDetailViewModel
import nl.mediaguard.app.ui.SeerrSearchViewModel
import nl.mediaguard.app.ui.StatusViewModel
import nl.mediaguard.app.ui.TodayViewModel
import nl.mediaguard.app.ui.nav.BottomBar
import nl.mediaguard.app.ui.nav.MAIN_TABS
import nl.mediaguard.app.ui.nav.TopBar
import nl.mediaguard.app.ui.screens.ActivityActions
import nl.mediaguard.app.ui.screens.ActivityContent
import nl.mediaguard.app.ui.screens.ActivityTab
import nl.mediaguard.app.ui.screens.LibraryContent
import nl.mediaguard.app.ui.screens.LibraryItemContent
import nl.mediaguard.app.ui.screens.OverviewActions
import nl.mediaguard.app.ui.screens.OverviewContent
import nl.mediaguard.app.ui.screens.PairingScreen
import nl.mediaguard.app.ui.screens.RequestSheetContent
import nl.mediaguard.app.ui.screens.RequestsActions
import nl.mediaguard.app.ui.screens.RequestsContent
import nl.mediaguard.app.ui.screens.RequestsTab
import nl.mediaguard.app.ui.screens.SettingsScreen
import nl.mediaguard.app.ui.screens.SpaceActions
import nl.mediaguard.app.ui.screens.SpaceContent
import nl.mediaguard.app.ui.theme.MediaGuardTheme
import nl.mediaguard.app.ui.theme.Mg
import nl.mediaguard.app.ui.theme.MgShape
import java.net.URLDecoder
import java.net.URLEncoder
import java.time.LocalDate

class MainActivity : ComponentActivity() {
    private val openTarget = mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        volgAppTaal()
        enableEdgeToEdge()
        openTarget.value = intent?.getStringExtra(EXTRA_OPEN)
        setContent { MediaGuardTheme { Root(openTarget.value) { openTarget.value = null } } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        openTarget.value = intent.getStringExtra(EXTRA_OPEN)
    }

    companion object { const val EXTRA_OPEN = "open" }
}

/** Meldingen openen een doel; oude namen ("today", "status") blijven werken. */
private fun routeForOpen(target: String): String = when (target) {
    "today" -> "activity?tab=gebeurd"
    "status" -> "overview"
    else -> target
}

@Composable
private fun Root(openTarget: String?, consumeOpen: () -> Unit) {
    val context = LocalContext.current
    val app = context.applicationContext as MediaGuardApp
    val session by app.session.collectAsStateWithLifecycle()

    if (!session.paired || session.rejected) {
        val reason = when {
            session.paired && session.rejected -> PairingReason.KeyRejected
            app.state.storageReset -> PairingReason.StorageReset
            else -> PairingReason.New
        }
        Scaffold(containerColor = Mg.colors.bg) { pad ->
            Box(Modifier.padding(pad)) { PairingScreen(reason) {} }
        }
        return
    }
    // Nieuwe koppeling = nieuwe generatie: schermen, navigatie en ViewModels beginnen opnieuw met de
    // nieuwe sleutel (geen oude StatusViewModel met de ingetrokken sleutel meer).
    key(session.generation) {
        val repo = app.repository()
        if (repo != null) MainUi(app, repo, session.generation, openTarget, consumeOpen)
        else Scaffold(containerColor = Mg.colors.bg) { pad ->
            // Adres of sleutel onderweg kwijt (opslag gewist): koppelscherm, nooit een leeg scherm.
            Box(Modifier.padding(pad)) {
                PairingScreen(if (app.settings.recovered) PairingReason.StorageReset else PairingReason.New) {}
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MainUi(app: MediaGuardApp, repo: Repository, generation: Int, openTarget: String?, consumeOpen: () -> Unit) {
    val context = LocalContext.current
    val nav = rememberNavController()
    val snackbar = remember { SnackbarHostState() }
    val entry by nav.currentBackStackEntryAsState()
    val route = entry?.destination?.route ?: ""

    val notifPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    fun openWeb(href: String) {
        val base = app.settings.serverUrl ?: return
        val url = if (href.startsWith("http")) href else base.trimEnd('/') + "/" + href.trimStart('/')
        runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
    }

    // Gedeeld over Overzicht, Activiteit, Ruimte en de bovenbalk (één bron, één poll).
    val appVersion = remember { BuildConfigInfo.versionName(context) }
    val statusVm: StatusViewModel = viewModel(key = "status-$generation") { StatusViewModel(repo, appVersion) }
    val recentVm: RecentViewModel = viewModel(key = "recent-$generation") { RecentViewModel(repo) }
    val status by statusVm.state.collectAsStateWithLifecycle()
    val me by statusVm.me.collectAsStateWithLifecycle()
    val missing by statusVm.missing.collectAsStateWithLifecycle()
    val freshness by statusVm.freshness.collectAsStateWithLifecycle()
    val update by statusVm.update.collectAsStateWithLifecycle()
    val statusMessage by statusVm.message.collectAsStateWithLifecycle()
    val notice by app.notice.collectAsStateWithLifecycle()
    var phone by remember { mutableStateOf(PhoneChecks.read(context)) }
    LifecycleResumeEffect(Unit) {
        phone = PhoneChecks.read(context)
        statusVm.startPolling()
        onPauseOrDispose { statusVm.stopPolling() }
    }
    LaunchedEffect(statusMessage) { statusMessage?.let { snackbar.showSnackbar(it); statusVm.consumeMessage() } }
    LaunchedEffect(notice) { notice?.let { snackbar.showSnackbar(it); app.consumeNotice() } }
    LaunchedEffect(openTarget) {
        if (openTarget != null) { nav.navigateTab(routeForOpen(openTarget)); consumeOpen() }
    }
    val scope = rememberCoroutineScope()
    val showMessage: (String) -> Unit = { msg -> scope.launch { snackbar.showSnackbar(msg) } }

    // Bevestigingen voor bediening (noodstop, baan pauzeren en knoppen die bestanden raken vragen altijd eerst).
    var stopDialog by remember { mutableStateOf<Boolean?>(null) }
    var laneDialog by remember { mutableStateOf<String?>(null) }
    var actionDialog by remember { mutableStateOf<AttentionAction?>(null) }
    var sheet by remember { mutableStateOf<Pair<String, Long>?>(null) }
    val runAction: (AttentionAction) -> Unit = { act -> if (act.needsConfirm) actionDialog = act else statusVm.runAction(act) }

    val tabRoute = MAIN_TABS.firstOrNull { route.substringBefore('?') == it.route.substringBefore('?') }
    val current = (status as? nl.mediaguard.app.ui.UiState.Data)?.value
    val attention = current?.attention
    val now = System.currentTimeMillis()
    val extras = OverviewExtras(
        staleSince = Staleness.staleSince(freshness, now),
        staleError = freshness.error,
        phoneWarning = phone.warning(app.settings.ntfyEnabled),
        // Staat de nieuwe versie al als punt in "Wacht op jou", dan hier alleen "app ouder dan de server".
        update = (if (attention?.items?.any { it.key == "system.update_available" } == true) update?.withoutLatest()
            else update)?.headline(),
    )

    Scaffold(
        containerColor = Mg.colors.bg,
        contentWindowInsets = WindowInsets(0),
        topBar = {
            TopBar(
                title = tabRoute?.let { null } ?: titleFor(route),
                attentionCount = attention?.count ?: 0,
                attentionErrors = attention?.errors ?: 0,
                initials = initials(me?.username),
                onBack = if (tabRoute == null && route.isNotEmpty()) ({ nav.popBackStack() }) else null,
                onSearch = { nav.navigateTab("library") },
                onAttention = { nav.navigateTab("activity?tab=problemen") },
                onProfile = { nav.navigate("settings") { launchSingleTop = true } },
            )
        },
        bottomBar = { BottomBar(route) { nav.navigateTab(it) } },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        NavHost(nav, startDestination = "overview", modifier = Modifier.padding(pad).background(Mg.colors.bg)) {
            composable("overview") {
                val recent by recentVm.state.collectAsStateWithLifecycle()
                OverviewContent(status, missing, recent, OverviewActions(
                    refresh = { statusVm.refresh(); statusVm.refreshExtras(); recentVm.refresh() },
                    openAttention = { nav.navigateTab("activity?tab=problemen") },
                    openActivity = { nav.navigateTab("activity?tab=nu") },
                    openHappened = { nav.navigateTab("activity?tab=gebeurd") },
                    openLibrary = { nav.navigateTab("library") },
                    openMissing = { nav.navigateTab("library?filter=missing") },
                    openSpace = { nav.navigateTab("space") },
                    openSettings = { nav.navigate("settings") { launchSingleTop = true } },
                    openWeb = ::openWeb,
                    retryJob = { statusVm.retryJob(it) },
                    action = runAction,
                ), extras)
            }
            composable("activity?tab={tab}", listOf(navArgument("tab") { type = NavType.StringType; nullable = true; defaultValue = null })) { e ->
                var tab by rememberSaveable { mutableStateOf(ActivityTab.of(e.arguments?.getString("tab")).key) }
                LaunchedEffect(e.arguments?.getString("tab")) { e.arguments?.getString("tab")?.let { tab = it } }
                val dayVm: TodayViewModel = viewModel(key = "day") { TodayViewModel(repo) }
                val historyVm: HistoryViewModel = viewModel { HistoryViewModel(repo) }
                val day by dayVm.state.collectAsStateWithLifecycle()
                val date by dayVm.date.collectAsStateWithLifecycle()
                val history by historyVm.state.collectAsStateWithLifecycle()
                val currentTab = ActivityTab.of(tab)
                LaunchedEffect(currentTab) { if (currentTab == ActivityTab.Geschiedenis) historyVm.ensureLoaded() }
                ActivityContent(currentTab, status, date, day, history, ActivityActions(
                    selectTab = { tab = it.key },
                    refresh = {
                        when (currentTab) {
                            ActivityTab.Gebeurd -> dayVm.refresh()
                            ActivityTab.Geschiedenis -> historyVm.refresh()
                            else -> statusVm.refresh()
                        }
                    },
                    setDate = { dayVm.setDate(it) },
                    pickDate = {
                        DatePickerDialog(context, { _, y, m, d -> dayVm.setDate(LocalDate.of(y, m + 1, d)) },
                            date.year, date.monthValue - 1, date.dayOfMonth).show()
                    },
                    openWeb = ::openWeb,
                    retryJob = { statusVm.retryJob(it) },
                    action = runAction,
                ))
            }
            composable("library?filter={filter}", listOf(navArgument("filter") { type = NavType.StringType; nullable = true; defaultValue = null })) { e ->
                val filter = e.arguments?.getString("filter")
                val vm: LibrarySearchViewModel = viewModel { LibrarySearchViewModel(repo, missingOnly = filter == "missing") }
                LaunchedEffect(filter) { vm.setMissingOnly(filter == "missing") }
                val state by vm.state.collectAsStateWithLifecycle()
                val kind by vm.kind.collectAsStateWithLifecycle()
                val missingOnly by vm.missingOnly.collectAsStateWithLifecycle()
                var q by rememberSaveable { mutableStateOf("") }
                LibraryContent(kind, state, q, { q = it }, { vm.search(q) }, { vm.setKind(it) },
                    { item -> nav.navigate("library/${item.id}?title=${URLEncoder.encode(item.title, "UTF-8")}") },
                    { vm.refresh() }, missingOnly = missingOnly, onShowAll = { vm.setMissingOnly(false) })
            }
            composable("library/{id}?title={title}", listOf(
                navArgument("id") { type = NavType.LongType },
                navArgument("title") { type = NavType.StringType; nullable = true; defaultValue = null },
            )) { e ->
                val id = e.arguments?.getLong("id") ?: 0
                val title = e.arguments?.getString("title")?.let { URLDecoder.decode(it, "UTF-8") }
                val vm: LibraryItemViewModel = viewModel(key = "lib-$id") { LibraryItemViewModel(repo, id) }
                val state by vm.state.collectAsStateWithLifecycle()
                val message by vm.message.collectAsStateWithLifecycle()
                LaunchedEffect(message) { message?.let { snackbar.showSnackbar(it); vm.consumeMessage() } }
                var seasonOn by remember { mutableStateOf<Int?>(null) }
                LibraryItemContent(title, state, { season, on -> if (on) seasonOn = season else vm.setSeason(season, false) }, { vm.refresh() })
                seasonOn?.let { season ->
                    ConfirmDialog(
                        title = if (season == 0) tr("Specials volgen?", "Monitor specials?") else tr("Seizoen $season volgen?", "Monitor season $season?"),
                        text = tr("MediaGuard houdt dit seizoen bij en haalt ontbrekende afleveringen op volgens je eisen.", "MediaGuard tracks this season and fetches missing episodes according to your requirements."),
                        confirmLabel = tr("Volgen en meteen zoeken", "Monitor and search now"),
                        onConfirm = { vm.setSeason(season, true, search = true); seasonOn = null },
                        secondaryLabel = tr("Alleen volgen", "Monitor only"),
                        onSecondary = { vm.setSeason(season, true, search = false); seasonOn = null },
                        onDismiss = { seasonOn = null },
                    )
                }
            }
            composable("space") {
                val recent by recentVm.state.collectAsStateWithLifecycle()
                SpaceContent(status, recent, SpaceActions(
                    refresh = { statusVm.refresh(); recentVm.refresh() },
                    emergency = { stopDialog = it },
                    lane = { lane, paused -> if (paused) statusVm.lane(lane, pause = false) else laneDialog = lane },
                    autopilot = { statusVm.autopilot() },
                    openHappened = { nav.navigateTab("activity?tab=gebeurd") },
                ))
            }
            composable("requests") {
                val search: SeerrSearchViewModel = viewModel { SeerrSearchViewModel(repo) }
                val mineVm: MyRequestsViewModel = viewModel { MyRequestsViewModel(repo) }
                val results by search.state.collectAsStateWithLifecycle()
                val mine by mineVm.state.collectAsStateWithLifecycle()
                var tab by rememberSaveable { mutableStateOf(RequestsTab.Ontdekken) }
                var q by rememberSaveable { mutableStateOf("") }
                RequestsContent(tab, q, results, mine, RequestsActions(
                    selectTab = { tab = it; if (it == RequestsTab.Mijn) mineVm.refresh() },
                    query = { q = it },
                    search = { search.search(q) },
                    open = { type, id -> sheet = type to id },
                    refreshMine = { mineVm.refresh() },
                ))
            }
            composable("settings") {
                SettingsScreen(me?.username, current?.version?.ifBlank { null }, update, showMessage) {}
            }
        }
    }

    sheet?.let { (type, id) ->
        val vm: SeerrDetailViewModel = viewModel(key = "seerr-$generation-$type-$id") { SeerrDetailViewModel(repo, type, id) }
        val state by vm.state.collectAsStateWithLifecycle()
        val message by vm.message.collectAsStateWithLifecycle()
        LaunchedEffect(message) { message?.let { snackbar.showSnackbar(it); vm.consumeMessage() } }
        val chosen = remember(type, id) { mutableStateListOf<Int>() }
        ModalBottomSheet(onDismissRequest = { sheet = null }, containerColor = Mg.peil.popover, shape = MgShape.sheetTop) {
            RequestSheetContent(state, chosen, { vm.request(it) }, { vm.refresh() })
        }
    }

    stopDialog?.let { turnOn ->
        var reason by remember { mutableStateOf("") }
        ConfirmDialog(
            title = if (turnOn) tr("Noodstop aanzetten?", "Turn on emergency stop?") else tr("Noodstop opheffen?", "Release emergency stop?"),
            text = if (turnOn) tr("Nieuwe grabs, encodes en vervangingen stoppen. Lopende encodes pauzeren.", "New grabs, encodes and replacements stop. Running encodes pause.")
            else tr("Alles gaat verder: gepauzeerde encodes hervatten, downloads komen rustig op gang.", "Everything continues: paused encodes resume, downloads pick up gradually."),
            confirmLabel = if (turnOn) tr("Noodstop aanzetten", "Turn on emergency stop") else tr("Noodstop opheffen", "Release emergency stop"),
            danger = turnOn,
            onConfirm = { statusVm.emergency(turnOn, reason); stopDialog = null },
            onDismiss = { stopDialog = null },
            extra = if (turnOn) ({ OutlinedTextField(reason, { reason = it }, label = { Text(tr("Reden (komt in de audit)", "Reason (goes into the audit log)")) }) }) else null,
        )
    }
    laneDialog?.let { lane ->
        ConfirmDialog(
            title = tr("${lane.uppercase()}-baan pauzeren?", "Pause ${lane.uppercase()} lane?"),
            text = tr("Er starten geen nieuwe encodes op deze baan tot je hem hervat. Lopende encodes maken hun werk af.", "No new encodes start on this lane until you resume it. Running encodes finish their work."),
            confirmLabel = tr("Baan pauzeren", "Pause lane"),
            onConfirm = { statusVm.lane(lane, pause = true); laneDialog = null },
            onDismiss = { laneDialog = null },
        )
    }
    actionDialog?.let { act ->
        ConfirmDialog(
            title = "${act.label}?",
            text = act.confirm?.ifBlank { null } ?: tr("MediaGuard voert ‘${act.label}’ nu uit, net als de knop in de web. Twijfel je, bekijk het dan eerst in de web.", "MediaGuard runs ‘${act.label}’ now, just like the button in the web UI. If in doubt, check it in the web UI first."),
            confirmLabel = act.label,
            onConfirm = { statusVm.runAction(act); actionDialog = null },
            onDismiss = { actionDialog = null },
        )
    }
}

/** Subpagina's tonen alleen een terugknop; de titel staat als paginakop in de inhoud (zoals `.back-link` in de web). */
private fun titleFor(route: String): String = when {
    route.startsWith("library/") -> tr("Bibliotheek", "Library")
    else -> ""
}

/** Naar een hoofdonderdeel; met een expliciete tab (bv. activity?tab=problemen) niet de oude staat herstellen. */
private fun NavHostController.navigateTab(route: String) {
    val explicit = route.contains('?')
    navigate(route) {
        popUpTo(graph.findStartDestination().id) { saveState = !explicit }
        launchSingleTop = true
        restoreState = !explicit
    }
}
