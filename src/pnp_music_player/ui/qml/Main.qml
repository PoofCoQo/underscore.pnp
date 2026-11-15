import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "components"

ApplicationWindow {
    id: window
    width: 1280
    height: 800
    minimumWidth: 900
    minimumHeight: 650
    visible: true
    title: "underscore.pnp"
    color: "#0c1117"
    property bool allowClose: false
    property url pendingOpenUrl: ""
    property bool pendingOpenIsRestore: false
    property url pendingRelinkDocument: ""
    property url pendingRelinkAudio: ""
    property bool pendingRelinkReplace: false
    property string currentMode: "player"
    // Break these initial context-property bindings in Component.onCompleted.
    // The retained QObject references then remain valid while QML tears down.
    property var setupBackendObject: setupBackend
    property var playerBackendObject: playerBackend
    property var shutdownBackendObject: shutdownBackend
    readonly property bool keyboardShortcutsEnabled:
        window.playerBackendObject !== null
        && !window.shutdownBackendObject.closing
        && window.currentMode !== "setup" && !settingsView.keyboardInputActive

    Connections {
        target: window.shutdownBackendObject
        function onReady() { window.close() }
    }

    Connections {
        target: window.setupBackendObject
        function onSourceHashMismatch() {
            sourceHashMismatchDialog.open()
        }
        function onMetadataProblem() {
            metadataProblemDialog.open()
        }
        function onRelinkCompleted() {
            window.playerBackendObject.refreshLibrary()
            window.setupBackendObject.refreshArchive()
            importPreviewDialog.markImported()
        }
        function onRelinkFailed() {
            importPreviewDialog.importFailed()
        }
    }

    function configuredKeybinding(action) {
        if (!window.playerBackendObject)
            return ""
        let revisionDependency = window.playerBackendObject.keybindings.length
        return window.playerBackendObject.keybinding(action)
               + (revisionDependency >= 0 ? "" : "")
    }

    function toggleFullscreen() {
        let fullscreen = window.visibility !== Window.FullScreen
        window.playerBackendObject.setFullscreenPreferred(fullscreen)
        fullscreen ? window.showFullScreen() : window.showNormal()
    }

    function requestOpenAudio(sourceUrl) {
        window.pendingRelinkDocument = ""
        window.pendingOpenUrl = sourceUrl
        window.pendingOpenIsRestore = false
        if (window.setupBackendObject.dirty) {
            openDialog.open()
        } else {
            continuePendingOpen()
        }
    }

    function requestRestoreArchived(sourceUrl) {
        window.pendingOpenUrl = sourceUrl
        window.pendingOpenIsRestore = true
        archivePopup.close()
        if (window.setupBackendObject.dirty)
            openDialog.open()
        else
            continuePendingOpen()
    }

    function continuePendingOpen() {
        if (window.pendingRelinkDocument.toString().length > 0) {
            let document = window.pendingRelinkDocument
            let audio = window.pendingRelinkAudio
            let replace = window.pendingRelinkReplace
            window.pendingRelinkDocument = ""
            window.pendingRelinkAudio = ""
            window.pendingRelinkReplace = false
            if (replace)
                window.setupBackendObject.beginImportedConfig(document, audio, true)
            else
                window.setupBackendObject.beginRelink(document, audio)
            return
        }
        if (window.pendingOpenIsRestore) {
            if (window.setupBackendObject.restoreArchived(window.pendingOpenUrl)) {
                window.playerBackendObject.refreshLibrary()
                window.setupBackendObject.openFile(window.pendingOpenUrl)
            }
            return
        }
        let archived = window.setupBackendObject.hasArchivedConfiguration(
            window.pendingOpenUrl)
        if (window.setupBackendObject.metadataProblemPath.length > 0)
            return
        if (archived) {
            archivedConfigDialog.open()
            return
        }
        window.setupBackendObject.openFile(window.pendingOpenUrl)
    }

    function openTrackInSetup(trackKey) {
        let sourceUrl = window.playerBackendObject.trackFileUrl(trackKey)
        if (sourceUrl.length === 0)
            return
        window.currentMode = "setup"
        requestOpenAudio(sourceUrl)
    }

    function requestRelink(documentUrl) {
        if (!documentUrl || documentUrl.toString().length === 0)
            return
        window.currentMode = "setup"
        window.pendingRelinkDocument = documentUrl
        relinkFileDialog.open()
    }

    function requestImportPair(documentUrl, audioUrl, replaceExisting) {
        window.currentMode = "setup"
        window.pendingRelinkDocument = documentUrl
        window.pendingRelinkAudio = audioUrl
        window.pendingRelinkReplace = replaceExisting
        if (window.setupBackendObject.dirty)
            openDialog.open()
        else
            window.continuePendingOpen()
    }

    palette.window: "#0c1117"
    palette.windowText: "#e7eef5"
    palette.base: "#17202a"
    palette.alternateBase: "#202b36"
    palette.text: "#e7eef5"
    palette.button: "#253342"
    palette.buttonText: "#f1f6fa"
    palette.highlight: "#29a99b"

    header: ToolBar {
        enabled: !window.shutdownBackendObject.closing
        implicitHeight: 60
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 12
            spacing: 10
            Label { text: "underscore.pnp"; font.bold: true; font.pixelSize: 20 }
            TouchButton { text: "PLAYER"; checkable: true; checked: window.currentMode === "player"; onClicked: { window.setupBackendObject.stopPreview(); window.currentMode = "player"; window.playerBackendObject.refreshLibrary() } }
            TouchButton { text: "SETUP"; checkable: true; checked: window.currentMode === "setup"; onClicked: window.currentMode = "setup" }
            TouchButton { text: "SETTINGS"; checkable: true; checked: window.currentMode === "settings"; onClicked: { window.setupBackendObject.stopPreview(); window.currentMode = "settings"; window.playerBackendObject.refreshOutputs() } }
            Item { Layout.fillWidth: true }
            TouchButton { visible: window.currentMode === "setup"; text: "Open…"; onClicked: fileDialog.open() }
            TouchButton { visible: window.currentMode === "setup"; text: "Import Config…"; onClicked: metadataImportDialog.open() }
            TouchButton { visible: window.currentMode === "setup"; text: "Export…"; onClicked: exportDialog.openDialog() }
            TouchButton {
                visible: window.currentMode === "setup"
                text: "Archived…"
                onClicked: {
                    window.setupBackendObject.refreshArchive()
                    archivePopup.open()
                }
            }
            Label { visible: window.currentMode === "setup" && window.setupBackendObject.temporaryRelink; text: "Session-only link"; color: "#ffca64" }
            TouchButton { objectName: "setupSaveButton"; visible: window.currentMode === "setup"; text: "Save"; enabled: window.setupBackendObject.hasTrack && window.setupBackendObject.dirty && !window.setupBackendObject.temporaryRelink; onClicked: window.setupBackendObject.save() }
            TouchButton { visible: window.currentMode === "setup"; text: "Discard"; enabled: window.setupBackendObject.hasTrack && window.setupBackendObject.dirty; onClicked: window.setupBackendObject.discard() }
            TouchButton {
                objectName: "audioCacheHeaderLabel"
                text: window.playerBackendObject
                      ? window.playerBackendObject.audioCacheShortText
                      : "RAM – / – MiB"
                font.pixelSize: 14
                font.bold: true
                baseColor: "#1d2a35"
                Layout.rightMargin: 4
                onClicked: cachePopup.open()
            }
            TouchButton {
                text: window.visibility === Window.FullScreen ? "Windowed" : "Fullscreen"
                onClicked: window.toggleFullscreen()
            }
        }
    }

    Shortcut {
        sequence: window.configuredKeybinding("pause_resume")
        enabled: window.keyboardShortcutsEnabled
                 && (window.playerBackendObject
                     ? window.playerBackendObject.playing : false)
        onActivated: window.playerBackendObject.pauseResume()
    }
    Shortcut {
        sequence: window.configuredKeybinding("stop")
        enabled: window.keyboardShortcutsEnabled
                 && (window.playerBackendObject
                     ? window.playerBackendObject.playing : false)
        onActivated: window.playerBackendObject.stop()
    }
    Shortcut {
        sequence: window.configuredKeybinding("go_now")
        enabled: window.keyboardShortcutsEnabled
                 && (window.playerBackendObject
                     ? window.playerBackendObject.canGoNow : false)
        onActivated: window.playerBackendObject.goNow()
    }
    Shortcut {
        sequence: window.configuredKeybinding("transition_normal")
        enabled: window.keyboardShortcutsEnabled
        onActivated: window.playerBackendObject.setTransitionMode("normal")
    }
    Shortcut {
        sequence: window.configuredKeybinding("transition_cut")
        enabled: window.keyboardShortcutsEnabled
        onActivated: window.playerBackendObject.setTransitionMode("hard_cut")
    }
    Shortcut {
        sequence: window.configuredKeybinding("transition_fade")
        enabled: window.keyboardShortcutsEnabled
        onActivated: window.playerBackendObject.setTransitionMode("fade")
    }
    Shortcut {
        sequence: window.configuredKeybinding("transition_crossfade")
        enabled: window.keyboardShortcutsEnabled
        onActivated: window.playerBackendObject.setTransitionMode("crossfade")
    }
    Shortcut {
        sequence: window.configuredKeybinding("mute_local")
        enabled: window.keyboardShortcutsEnabled
        onActivated: window.playerBackendObject.toggleMute()
    }
    Shortcut {
        sequence: window.configuredKeybinding("mute_discord")
        enabled: window.keyboardShortcutsEnabled
        onActivated: window.playerBackendObject.toggleDiscordMute()
    }
    Shortcut {
        sequence: window.configuredKeybinding("fullscreen")
        enabled: window.playerBackendObject !== null
                 && !window.shutdownBackendObject.closing
                 && !settingsView.keyboardInputActive
        onActivated: window.toggleFullscreen()
    }

    Instantiator {
        id: favoriteShortcuts
        objectName: "favoriteShortcuts"
        model: 10
        delegate: Shortcut {
            required property int index
            objectName: "favoriteShortcut" + (index + 1)
            sequence: window.configuredKeybinding("favorite_" + (index + 1))
            enabled: window.keyboardShortcutsEnabled
            onActivated: window.playerBackendObject.queueFavorite(index + 1)
        }
    }

    Popup {
        id: cachePopup
        objectName: "audioCachePopup"
        anchors.centerIn: parent
        width: Math.min(440, window.width - 40)
        height: 170
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            radius: 10
            color: "#17222c"
            border.color: "#53697c"
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 10
            Label {
                text: "Audio Cache"
                font.pixelSize: 21
                font.bold: true
            }
            Label {
                text: window.playerBackendObject
                      ? window.playerBackendObject.audioCacheText
                      : "RAM cache unavailable"
                color: "#b8c8d6"
                font.pixelSize: 16
                Layout.fillWidth: true
            }
            Item { Layout.fillHeight: true }
            TouchButton {
                text: "Close"
                Layout.alignment: Qt.AlignRight
                onClicked: cachePopup.close()
            }
        }
    }

    StackLayout {
        enabled: !window.shutdownBackendObject.closing
        anchors.fill: parent
        currentIndex: window.currentMode === "player" ? 0
                    : window.currentMode === "setup" ? 1 : 2
        PlayerView {
            backend: window.playerBackendObject
            onOpenSetup: window.currentMode = "setup"
            onShowMetadataProblems: metadataProblemsPopup.open()
            onOpenTrackSetup: function(trackKey) {
                window.openTrackInSetup(trackKey)
            }
            onRelinkTrack: function(trackKey) {
                window.requestRelink(window.playerBackendObject.trackMetadataUrl(trackKey))
            }
        }
        SetupView {
            backend: window.setupBackendObject
            enabled: window.setupBackendObject.hasTrack && !window.setupBackendObject.busy
            opacity: enabled ? 1.0 : 0.55
            onTrackArchived: {
                window.playerBackendObject.refreshLibrary()
                window.setupBackendObject.refreshArchive()
            }
        }
        SettingsView {
            id: settingsView
            backend: window.playerBackendObject
        }
    }

    Popup {
        id: metadataProblemsPopup
        objectName: "metadataProblemsPopup"
        anchors.centerIn: parent
        width: Math.min(780, window.width - 40)
        height: Math.min(500, window.height - 40)
        modal: true
        focus: true
        background: Rectangle {
            radius: 10
            color: "#17222c"
            border.color: "#d19a4b"
        }
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            Label { text: "Metadata Problems"; font.pixelSize: 22; font.bold: true }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ColumnLayout {
                    width: metadataProblemsPopup.width - 40
                    Repeater {
                        model: window.playerBackendObject
                               ? window.playerBackendObject.metadataProblems : []
                        ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Label { text: modelData.path; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true; color: "#ffca64" }
                            Label { text: modelData.reason; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                    }
                }
            }
            TouchButton {
                text: "Close"
                Layout.alignment: Qt.AlignRight
                onClicked: metadataProblemsPopup.close()
            }
        }
    }

    Popup {
        id: archivePopup
        objectName: "archiveBrowserPopup"
        anchors.centerIn: parent
        width: Math.min(760, window.width - 48)
        height: Math.min(600, window.height - 80)
        padding: 0
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            radius: 10
            color: "#17222c"
            border.width: 1
            border.color: "#53697c"
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 12
            Label {
                text: "Archived Tracks"
                font.pixelSize: 23
                font.bold: true
            }
            Label {
                text: "Restore a track, or locate its missing audio file to reactivate it."
                color: "#9eb0c2"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 7
                color: "#101820"
                border.color: "#34495c"

                ListView {
                    id: archiveList
                    objectName: "archiveTrackList"
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    spacing: 8
                    model: window.setupBackendObject
                           ? window.setupBackendObject.archivedTracks : []
                    delegate: Rectangle {
                        required property var modelData
                        width: archiveList.width
                        height: modelData.mediaAvailable ? 82 : 104
                        radius: 7
                        color: "#1b2935"
                        border.color: "#405466"
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 10
                            ColumnLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: modelData.title
                                    font.pixelSize: 17
                                    font.bold: true
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Label {
                                    text: modelData.relativePath
                                    color: "#8fa4b8"
                                    elide: Text.ElideMiddle
                                    Layout.fillWidth: true
                                }
                                Label {
                                    visible: !modelData.mediaAvailable
                                    text: "Audio file missing"
                                    color: "#ffca64"
                                }
                                Label {
                                    visible: modelData.artist.length > 0
                                    text: modelData.artist
                                    color: "#aebdcc"
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }
                            TouchButton {
                                text: modelData.mediaAvailable ? "Restore" : "Locate…"
                                Layout.preferredWidth: 112
                                Layout.preferredHeight: 48
                                onClicked: {
                                    if (modelData.mediaAvailable)
                                        window.requestRestoreArchived(modelData.sourceUrl)
                                    else {
                                        archivePopup.close()
                                        window.requestRelink(modelData.documentUrl)
                                    }
                                }
                            }
                        }
                    }
                    ScrollBar.vertical: ScrollBar {
                        policy: ScrollBar.AsNeeded
                        interactive: true
                        width: 20
                    }
                }
                Label {
                    visible: archiveList.count === 0
                    anchors.centerIn: parent
                    text: "No archived tracks."
                    color: "#8fa4b8"
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                TouchButton { text: "Close"; onClicked: archivePopup.close() }
            }
        }
    }

    Popup {
        id: archivedConfigDialog
        objectName: "archivedConfigurationPrompt"
        anchors.centerIn: parent
        width: Math.min(600, window.width - 40)
        height: 260
        padding: 0
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape

        background: Rectangle {
            radius: 10
            color: "#17222c"
            border.width: 1
            border.color: "#60798e"
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 12
            Label {
                text: "Archived Configuration Found"
                font.pixelSize: 22
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: "#b9c7d6"
                text: "This media file has an archived configuration. Restore it, or analyze the file as a new track while keeping the archived copy."
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                TouchButton {
                    text: "Cancel"
                    onClicked: archivedConfigDialog.close()
                }
                Item { Layout.fillWidth: true }
                TouchButton {
                    text: "Analyze as New"
                    onClicked: {
                        archivedConfigDialog.close()
                        window.setupBackendObject.openFile(window.pendingOpenUrl)
                    }
                }
                TouchButton {
                    text: "Restore & Open"
                    baseColor: "#2d6359"
                    onClicked: {
                        archivedConfigDialog.close()
                        window.pendingOpenIsRestore = true
                        window.continuePendingOpen()
                    }
                }
            }
        }
    }

    Popup {
        id: metadataProblemDialog
        objectName: "metadataProblemDialog"
        anchors.centerIn: parent
        width: Math.min(720, window.width - 40)
        height: 340
        modal: true
        focus: true
        closePolicy: Popup.NoAutoClose
        background: Rectangle {
            radius: 10
            color: "#17222c"
            border.width: 1
            border.color: "#d19a4b"
        }
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 12
            Label {
                text: window.setupBackendObject.metadataProblemIsUnsupported
                      ? "Unsupported Metadata Version" : "Metadata Cannot Be Loaded"
                color: "#ffca64"
                font.pixelSize: 22
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
                text: window.setupBackendObject.metadataProblemPath
                color: "#9fb2c4"
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: window.setupBackendObject.metadataProblemReason
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: "#9fb2c4"
                text: window.setupBackendObject.metadataProblemIsUnsupported
                      ? "This file may have been created by a newer app version. Start Fresh ignores it and backs it up if you save a new configuration. The audio file is not changed."
                      : "Start Fresh ignores this configuration. If you save the new track, the original JSON is backed up first. The audio file is not changed."
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                TouchButton {
                    objectName: "metadataProblemDontOpenButton"
                    text: "Don't Open"
                    Layout.preferredHeight: 54
                    onClicked: {
                        if (window.setupBackendObject.resolveMetadataProblem("dont_open"))
                            metadataProblemDialog.close()
                    }
                }
                Item { Layout.fillWidth: true }
                TouchButton {
                    objectName: "metadataProblemStartFreshButton"
                    text: "Start Fresh"
                    baseColor: "#805d2f"
                    Layout.preferredHeight: 54
                    onClicked: {
                        if (window.setupBackendObject.resolveMetadataProblem("start_fresh"))
                            metadataProblemDialog.close()
                    }
                }
            }
        }
    }

    Popup {
        id: sourceHashMismatchDialog
        objectName: "sourceHashMismatchDialog"
        anchors.centerIn: parent
        width: Math.min(720, window.width - 40)
        height: 390
        padding: 0
        modal: true
        focus: true
        closePolicy: Popup.NoAutoClose

        background: Rectangle {
            radius: 10
            color: "#17222c"
            border.width: 1
            border.color: "#d19a4b"
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 12
            Label {
                text: "Audio File Does Not Match"
                color: "#ffca64"
                font.pixelSize: 22
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: "#d7e0e8"
                text: "The saved metadata belongs to a different version of “"
                      + window.setupBackendObject.sourceHashMismatchFileName
                      + "”. Marker positions and loops may not fit this audio file."
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 76
                radius: 7
                color: "#101820"
                border.color: "#354b5e"
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 4
                    Label {
                        text: "Metadata: " + window.setupBackendObject.sourceHashExpected.substring(0, 16) + "…"
                        color: "#9fb2c4"
                        font.family: "monospace"
                    }
                    Label {
                        text: "Audio file: " + window.setupBackendObject.sourceHashActual.substring(0, 16) + "…"
                        color: "#9fb2c4"
                        font.family: "monospace"
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: "#9fb2c4"
                text: window.setupBackendObject.relinking
                      ? "Open Once uses this audio only for this session and disables Save. Relink & Replace Hash permanently associates this configuration with the selected file after analysis."
                      : "Open Once keeps the saved hash unchanged. Overwrite Hash permanently associates this metadata with the selected file."
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                TouchButton {
                    objectName: "sourceHashDontOpenButton"
                    text: "Don't Open"
                    Layout.preferredHeight: 54
                    onClicked: {
                        if (window.setupBackendObject.resolveSourceHashMismatch(
                                "dont_open"))
                            sourceHashMismatchDialog.close()
                    }
                }
                Item { Layout.fillWidth: true }
                TouchButton {
                    objectName: "sourceHashOpenOnceButton"
                    text: "Open Once"
                    Layout.preferredHeight: 54
                    onClicked: {
                        if (window.setupBackendObject.resolveSourceHashMismatch(
                                "open_once"))
                            sourceHashMismatchDialog.close()
                    }
                }
                TouchButton {
                    objectName: "sourceHashOverwriteButton"
                    text: window.setupBackendObject.relinking ? "Relink & Replace Hash" : "Overwrite Hash"
                    baseColor: "#805d2f"
                    Layout.preferredHeight: 54
                    onClicked: {
                        if (window.setupBackendObject.resolveSourceHashMismatch(
                                "overwrite"))
                            sourceHashMismatchDialog.close()
                    }
                }
            }
        }
    }

    Rectangle {
        visible: window.currentMode === "setup" && window.setupBackendObject.busy
        anchors.centerIn: parent
        width: 420; height: 150; radius: 12
        color: "#e6222d38"; border.color: "#60798e"; z: 100
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 20
            Label { text: "Analyzing track: " + window.setupBackendObject.progressStage; font.pixelSize: 18 }
            ProgressBar { from: 0; to: 1; value: window.setupBackendObject.progress; Layout.fillWidth: true }
            Label { text: Math.round(window.setupBackendObject.progress * 100) + " %"; Layout.alignment: Qt.AlignHCenter }
        }
    }

    Rectangle {
        property var errorBackend: window.currentMode === "setup"
                                   ? window.setupBackendObject
                                   : window.playerBackendObject
        property string currentError: errorBackend ? errorBackend.errorMessage : ""
        visible: currentError.length > 0
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        height: errorLabel.implicitHeight + 24; color: "#982c38"; z: 120
        Label { id: errorLabel; anchors.fill: parent; anchors.margins: 12; text: parent.currentError; wrapMode: Text.Wrap }
    }

    FileDialog {
        id: fileDialog
        title: "Open Audio File"
        currentFolder: window.setupBackendObject.musicDirectoryUrl
        nameFilters: ["Audio (*.wav *.flac *.ogg *.mp3)", "All Files (*)"]
        onAccepted: {
            window.requestOpenAudio(selectedFile)
        }
    }

    FileDialog {
        id: metadataImportDialog
        title: "Import Metadata Configurations"
        currentFolder: window.setupBackendObject.exportDirectoryUrl
        fileMode: FileDialog.OpenFiles
        nameFilters: ["Metadata JSON (*.json)"]
        onAccepted: {
            let paths = []
            for (let i = 0; i < selectedFiles.length; i++)
                paths.push(selectedFiles[i].toString())
            importPreviewDialog.openDocuments(paths)
        }
    }

    MetadataExportDialog {
        id: exportDialog
        objectName: "metadataExportDialog"
        parent: Overlay.overlay
        backend: window.setupBackendObject
        width: Math.min(window.width - 40, 960)
        height: Math.min(window.height - 40, 720)
        x: (window.width - width) / 2
        y: (window.height - height) / 2
    }

    MetadataImportDialog {
        id: importPreviewDialog
        objectName: "metadataImportPreviewDialog"
        parent: Overlay.overlay
        backend: window.setupBackendObject
        width: Math.min(window.width - 40, 850)
        height: Math.min(window.height - 40, 650)
        x: (window.width - width) / 2
        y: (window.height - height) / 2
        onImportRequested: function(document, audio, replaceExisting) {
            window.requestImportPair(document, audio, replaceExisting)
        }
    }

    FileDialog {
        id: relinkFileDialog
        title: "Locate Audio File"
        currentFolder: window.setupBackendObject.musicDirectoryUrl
        nameFilters: ["Audio (*.wav *.flac *.ogg *.mp3)", "All Files (*)"]
        onAccepted: {
            window.pendingRelinkAudio = selectedFile
            if (window.setupBackendObject.dirty)
                openDialog.open()
            else
                window.continuePendingOpen()
        }
        onRejected: window.pendingRelinkDocument = ""
    }

    Dialog {
        id: openDialog
        anchors.centerIn: parent
        title: "Unsaved Changes"
        modal: true
        standardButtons: Dialog.Save | Dialog.Discard | Dialog.Cancel
        Label { text: "Save changes before opening another file?" }
        onAccepted: {
            if (window.setupBackendObject.save())
                window.continuePendingOpen()
        }
        onDiscarded: {
            window.setupBackendObject.discard()
            window.continuePendingOpen()
        }
        onRejected: {
            if (window.pendingRelinkDocument.toString().length > 0) {
                window.pendingRelinkDocument = ""
                window.pendingRelinkAudio = ""
                window.pendingRelinkReplace = false
                importPreviewDialog.importFailed()
            }
        }
    }

    Dialog {
        id: closeDialog
        anchors.centerIn: parent
        title: "Unsaved Changes"
        modal: true
        standardButtons: Dialog.Save | Dialog.Discard | Dialog.Cancel
        Label { text: "Save changes before quitting?" }
        onAccepted: {
            if (window.setupBackendObject.save()) {
                window.allowClose = true
                window.close()
            }
        }
        onDiscarded: { window.allowClose = true; window.close() }
    }

    Popup {
        objectName: "closingPopup"
        anchors.centerIn: parent
        width: Math.min(420, window.width - 40)
        height: 160
        modal: true
        focus: true
        closePolicy: Popup.NoAutoClose
        visible: window.shutdownBackendObject.closing
                 && !window.shutdownBackendObject.complete
        ColumnLayout {
            anchors.fill: parent
            BusyIndicator { running: parent.visible; Layout.alignment: Qt.AlignHCenter }
            Label {
                text: "Closing… Waiting for background work to finish."
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    Dialog {
        id: settingsRecoveryDialog
        objectName: "settingsRecoveryDialog"
        anchors.centerIn: parent
        width: Math.min(600, window.width - 40)
        title: "Settings Recovered"
        modal: true
        standardButtons: Dialog.Ok
        Label {
            width: parent.width
            text: window.playerBackendObject
                  ? window.playerBackendObject.settingsRecoveryMessage : ""
            wrapMode: Text.Wrap
        }
    }

    onClosing: function(close) {
        if (window.shutdownBackendObject.complete)
            return
        close.accepted = false
        if (window.shutdownBackendObject.closing)
            return
        if (!allowClose && window.setupBackendObject.dirty)
            closeDialog.open()
        else
            window.shutdownBackendObject.request()
    }

    Component.onCompleted: {
        // A direct assignment removes the context-property bindings above.
        // Python owns both objects for at least as long as the QML engine.
        setupBackendObject = setupBackend
        playerBackendObject = playerBackend
        shutdownBackendObject = shutdownBackend
        if (playerBackendObject.fullscreenPreferred)
            window.showFullScreen()
        if (playerBackendObject && playerBackendObject.settingsRecoveryMessage.length > 0)
            settingsRecoveryDialog.open()
    }
}
