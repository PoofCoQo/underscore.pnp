import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "components"

Dialog {
    id: root
    property var backend
    property var candidates: []
    property var selectedSources: []
    property var plan: []
    property var overwriteDecisions: ({})
    property string folderUrl: ""
    property string resultText: ""
    property string searchText: ""
    property string folderWarning: ""

    modal: true
    title: ""
    padding: 18
    standardButtons: Dialog.NoButton
    background: Rectangle { color: "#17202a"; border.color: "#60798e"; radius: 12 }

    function openDialog() {
        folderWarning = backend.ensureExportDirectory() ? "" :
                        "The default export folder cannot be created. Choose another folder."
        candidates = backend.exportCandidates()
        selectedSources = backend.currentSource.length > 0
                          && candidates.some(item => item.source === backend.currentSource)
                          ? [backend.currentSource] : []
        folderUrl = backend.exportDirectoryUrl
        overwriteDecisions = ({})
        resultText = ""
        searchText = ""
        refreshPlan()
        open()
    }

    function refreshPlan() {
        plan = selectedSources.length > 0
             ? backend.previewExport(JSON.stringify(selectedSources), folderUrl) : []
        overwriteDecisions = ({})
        resultText = ""
    }

    function setSelected(source, selected) {
        let next = selectedSources.filter(item => item !== source)
        if (selected)
            next.push(source)
        selectedSources = next
        refreshPlan()
    }

    function setDecision(source, decision) {
        let next = Object.assign({}, overwriteDecisions)
        next[source] = decision
        overwriteDecisions = next
    }

    function setAllConflicts(decision) {
        let next = Object.assign({}, overwriteDecisions)
        for (let item of plan) {
            if (item.exists)
                next[item.source] = decision
        }
        overwriteDecisions = next
    }

    function performExport() {
        resultText = backend.exportSelected(
            JSON.stringify(selectedSources), folderUrl,
            JSON.stringify(overwriteDecisions))
        refreshAfterExport()
    }

    function refreshAfterExport() {
        let message = resultText
        plan = backend.previewExport(JSON.stringify(selectedSources), folderUrl)
        resultText = message
    }

    Dialog {
        id: replaceConfirmation
        modal: true
        title: "Replace Existing Metadata?"
        standardButtons: Dialog.NoButton
        anchors.centerIn: parent
        ColumnLayout {
            Label {
                text: "The selected JSON files will be permanently replaced. Audio files will not be touched."
                wrapMode: Text.Wrap
                Layout.preferredWidth: 400
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                TouchButton { text: "Cancel"; onClicked: replaceConfirmation.close() }
                TouchButton {
                    text: "Replace & Export"
                    onClicked: { replaceConfirmation.close(); root.performExport() }
                }
            }
        }
    }

    FolderDialog {
        id: targetFolderDialog
        title: "Choose Export Folder"
        currentFolder: root.folderUrl
        onAccepted: {
            root.folderUrl = selectedFolder.toString()
            root.folderWarning = ""
            root.refreshPlan()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        Label { text: "Export Metadata"; font.pixelSize: 24; font.bold: true; Layout.fillWidth: true }
        Label {
            text: "Select the tracks to share. Only JSON files are written; audio and caches are never included."
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        Label {
            visible: backend.dirty
            text: "Unsaved changes in the current track are not included. Save the track first to export those changes."
            color: "#ffca64"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        RowLayout {
            Layout.fillWidth: true
            TextField {
                Layout.fillWidth: true
                placeholderText: "Search artist, title or file name"
                text: root.searchText
                onTextChanged: root.searchText = text
            }
            TouchButton {
                text: "Select All"
                onClicked: {
                    root.selectedSources = root.candidates.filter(item => item.hasHash)
                                          .map(item => item.source)
                    root.refreshPlan()
                }
            }
            TouchButton { text: "Clear"; onClicked: { root.selectedSources = []; root.refreshPlan() } }
        }
        ScrollView {
            Layout.fillWidth: true
            Layout.preferredHeight: 190
            clip: true
            ColumnLayout {
                width: parent.width
                spacing: 4
                Repeater {
                    model: root.candidates.filter(item =>
                        (item.artist + " " + item.title + " " + item.fileName)
                        .toLowerCase().includes(root.searchText.toLowerCase()))
                    delegate: RowLayout {
                        required property var modelData
                        width: parent.width
                        spacing: 8
                        TouchButton {
                            text: root.selectedSources.includes(modelData.source) ? "✓" : "○"
                            enabled: modelData.hasHash
                            Layout.preferredWidth: 50
                            onClicked: root.setSelected(
                                modelData.source,
                                !root.selectedSources.includes(modelData.source))
                        }
                        Label {
                            text: (modelData.artist || "Unknown Artist") + " - " + modelData.title
                            Layout.fillWidth: true
                            elide: Text.ElideMiddle
                        }
                        Label {
                            text: modelData.hasHash ? "" : "Open to add audio hash"
                            color: "#ffca64"
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { text: "Destination: " + backend.localPathText(root.folderUrl); Layout.fillWidth: true; elide: Text.ElideMiddle }
            TouchButton { text: "Choose Folder…"; onClicked: targetFolderDialog.open() }
        }
        Label { visible: root.folderWarning.length > 0; text: root.folderWarning; color: "#ffca64"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Label {
            text: root.selectedSources.length + " selected · " + root.plan.filter(item => item.exists).length + " existing file(s)"
            color: "#91a5b9"
        }
        RowLayout {
            Layout.fillWidth: true
            visible: root.plan.some(item => item.exists)
            Label { text: "Existing files are skipped unless you explicitly choose Replace."; Layout.fillWidth: true; wrapMode: Text.Wrap }
            TouchButton { text: "Skip All"; onClicked: root.setAllConflicts("skip") }
            TouchButton { text: "Replace All"; onClicked: root.setAllConflicts("replace") }
        }
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ColumnLayout {
                width: parent.width
                spacing: 4
                Repeater {
                    model: root.plan
                    delegate: RowLayout {
                        required property var modelData
                        width: parent.width
                        spacing: 8
                        Label { text: modelData.fileName; Layout.fillWidth: true; elide: Text.ElideMiddle }
                        Label { text: modelData.exists ? "Already exists" : "New"; color: modelData.exists ? "#ffca64" : "#72e59b" }
                        TouchButton {
                            visible: modelData.exists
                            text: root.overwriteDecisions[modelData.source] === "replace" ? "Replace" : "Skip"
                            onClicked: root.setDecision(
                                modelData.source,
                                root.overwriteDecisions[modelData.source] === "replace"
                                ? "skip" : "replace")
                        }
                    }
                }
            }
        }
        Label { text: root.resultText; visible: text.length > 0; color: "#72e59b"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            TouchButton { text: "Close"; onClicked: root.close() }
            TouchButton {
                text: "Export " + root.selectedSources.length + " JSON File(s)"
                enabled: root.plan.length > 0
                onClicked: {
                    if (root.plan.some(item => item.exists
                                       && root.overwriteDecisions[item.source] === "replace"))
                        replaceConfirmation.open()
                    else
                        root.performExport()
                }
            }
        }
    }
}
