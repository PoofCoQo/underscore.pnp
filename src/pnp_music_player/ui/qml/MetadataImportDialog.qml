import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "components"

Dialog {
    id: root
    property var backend
    property var items: []
    property int choosingIndex: -1
    property string pendingDocument: ""
    property string pendingAudio: ""
    property var batchQueue: []
    property bool batchActive: false
    property string resultText: ""
    signal importRequested(string document, string audio, bool replaceExisting)

    modal: true
    title: ""
    padding: 18
    standardButtons: Dialog.NoButton
    background: Rectangle { color: "#17202a"; border.color: "#60798e"; radius: 12 }

    function openDocuments(urls) {
        let previews = backend.previewImports(JSON.stringify(urls))
        if (previews.length === 0)
            return
        items = previews.map(item => Object.assign({}, item, {
            audio: item.suggestedAudio,
            imported: false
        }))
        pendingDocument = ""
        batchQueue = []
        batchActive = false
        resultText = ""
        open()
    }

    function markImported() {
        if (pendingDocument.length === 0)
            return
        items = items.map(item => {
            let copy = Object.assign({}, item)
            if (copy.document === pendingDocument)
                copy.imported = true
            return copy
        })
        pendingDocument = ""
        if (batchActive)
            nextImportTimer.start()
        else
            open()
    }

    function importFailed() {
        if (pendingDocument.length === 0)
            return
        batchActive = false
        batchQueue = []
        pendingDocument = ""
        resultText = "Import stopped. Review the error, then retry or choose another audio file."
        open()
    }

    function importAllReady() {
        if (backend.dirty) {
            resultText = "Save or discard current setup changes before batch import."
            return
        }
        let seen = ({})
        batchQueue = items.filter(item => {
            if (item.imported || item.audio.length === 0
                    || backend.hasActiveMetadata(item.audio) || seen[item.audio])
                return false
            seen[item.audio] = true
            return true
        })
        if (batchQueue.length === 0) {
            resultText = "No unconfigured, uniquely matched audio files are ready."
            return
        }
        batchActive = true
        resultText = ""
        runNext()
    }

    function runNext() {
        if (batchQueue.length === 0) {
            batchActive = false
            resultText = "Ready files imported. Review any remaining rows individually."
            open()
            return
        }
        let next = batchQueue[0]
        batchQueue = batchQueue.slice(1)
        submitImport(next.document, next.audio, false)
    }

    Timer {
        id: nextImportTimer
        interval: 150
        repeat: true
        onTriggered: {
            if (!backend.busy) {
                stop()
                root.runNext()
            }
        }
    }

    function submitImport(document, audio, replaceExisting) {
        pendingDocument = document
        pendingAudio = ""
        close()
        importRequested(document, audio, replaceExisting)
    }

    Dialog {
        id: replaceConfirmation
        modal: true
        title: "Replace Existing Configuration?"
        standardButtons: Dialog.NoButton
        anchors.centerIn: parent
        ColumnLayout {
            Label {
                text: "This audio file already has a local configuration. Importing will replace it after the audio hash check. A backup of the previous JSON will be kept."
                wrapMode: Text.Wrap
                Layout.preferredWidth: 420
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                TouchButton {
                    text: "Cancel"
                    onClicked: {
                        replaceConfirmation.close()
                        root.pendingDocument = ""
                        root.pendingAudio = ""
                    }
                }
                TouchButton {
                    text: "Replace Configuration"
                    onClicked: {
                        replaceConfirmation.close()
                        root.submitImport(root.pendingDocument, root.pendingAudio, true)
                    }
                }
            }
        }
    }

    FileDialog {
        id: audioDialog
        title: "Locate Matching Audio File"
        currentFolder: backend.musicDirectoryUrl
        nameFilters: ["Audio (*.wav *.flac *.ogg *.mp3)", "All Files (*)"]
        onAccepted: {
            if (root.choosingIndex >= 0) {
                let next = root.items.slice()
                let item = Object.assign({}, next[root.choosingIndex])
                item.audio = selectedFile.toString()
                next[root.choosingIndex] = item
                root.items = next
            }
            root.choosingIndex = -1
        }
        onRejected: root.choosingIndex = -1
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        Label { text: "Import Metadata"; font.pixelSize: 24; font.bold: true; Layout.fillWidth: true }
        Label {
            text: "Select the audio file for each configuration. Its SHA-256 hash is checked before import. No audio file is copied."
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ColumnLayout {
                width: parent.width
                spacing: 8
                Repeater {
                    model: root.items
                    delegate: Frame {
                        required property var modelData
                        required property int index
                        width: parent.width
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 5
                            Label {
                                text: (modelData.artist || "Unknown Artist") + " - " + modelData.title
                                font.bold: true
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                            }
                            Label {
                                text: "Expected audio: " + modelData.mediaName
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                            }
                            Label {
                                text: modelData.imported ? "Imported" :
                                      modelData.audio.length > 0 ? "Audio: " + backend.localPathText(modelData.audio) :
                                      modelData.matchCount > 1 ? "Multiple matching file names — choose one" :
                                      "Audio not found — choose a file"
                                color: modelData.imported ? "#72e59b" : "#ffca64"
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                            }
                            Label {
                                visible: !modelData.imported && modelData.audio.length > 0
                                         && backend.hasActiveMetadata(modelData.audio)
                                text: "A local configuration exists and will need confirmation before replacement."
                                color: "#ffca64"
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            RowLayout {
                                Layout.alignment: Qt.AlignRight
                                TouchButton {
                                    text: "Choose Audio…"
                                    enabled: !modelData.imported
                                    onClicked: { root.choosingIndex = index; audioDialog.open() }
                                }
                                TouchButton {
                                    text: "Import"
                                    enabled: !modelData.imported && modelData.audio.length > 0 && !backend.busy
                                    onClicked: {
                                        if (backend.hasActiveMetadata(modelData.audio)) {
                                            root.pendingDocument = modelData.document
                                            root.pendingAudio = modelData.audio
                                            replaceConfirmation.open()
                                        } else {
                                            root.submitImport(modelData.document, modelData.audio, false)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            Label { text: root.resultText; visible: text.length > 0; color: "#ffca64"; wrapMode: Text.Wrap; Layout.preferredWidth: 400 }
            TouchButton {
                text: "Import All Ready"
                enabled: !backend.busy && !root.batchActive
                onClicked: root.importAllReady()
            }
            TouchButton { text: "Close"; onClicked: root.close() }
        }
    }
}
