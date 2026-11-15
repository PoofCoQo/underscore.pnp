import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

Item {
    id: root
    objectName: "settingsView"
    required property var backend
    property var backendObject: backend
    property string capturingAction: ""
    readonly property bool keyCaptureActive: capturingAction.length > 0
    readonly property bool keyboardInputActive:
        keyCaptureActive || tokenField.activeFocus
    readonly property int actionHeight: 58

    Component.onCompleted: backendObject = backend

    function sequenceForEvent(event) {
        let parts = []
        if (event.modifiers & Qt.ControlModifier) parts.push("Ctrl")
        if (event.modifiers & Qt.AltModifier) parts.push("Alt")
        if (event.modifiers & Qt.ShiftModifier) parts.push("Shift")
        if (event.modifiers & Qt.MetaModifier) parts.push("Meta")
        let key = ""
        if (event.key >= Qt.Key_A && event.key <= Qt.Key_Z)
            key = String.fromCharCode(event.key)
        else if (event.key >= Qt.Key_0 && event.key <= Qt.Key_9)
            key = String.fromCharCode(event.key)
        else if (event.key >= Qt.Key_F1 && event.key <= Qt.Key_F12)
            key = "F" + (event.key - Qt.Key_F1 + 1)
        else if (event.key === Qt.Key_Space) key = "Space"
        else if (event.key === Qt.Key_Return) key = "Return"
        else if (event.key === Qt.Key_Enter) key = "Enter"
        else if (event.key === Qt.Key_Tab) key = "Tab"
        else if (event.key === Qt.Key_Left) key = "Left"
        else if (event.key === Qt.Key_Right) key = "Right"
        else if (event.key === Qt.Key_Up) key = "Up"
        else if (event.key === Qt.Key_Down) key = "Down"
        else if (event.key === Qt.Key_Plus) key = "+"
        else if (event.key === Qt.Key_Minus) key = "-"
        if (key.length === 0)
            return ""
        parts.push(key)
        return parts.join("+")
    }

    function commitCapturedBinding(actionId, sequence) {
        // This function belongs to the persistent view, not to a delegate.
        // setKeybinding rebuilds the Repeater synchronously.
        if (backendObject.setKeybinding(actionId, sequence)) {
            capturingAction = ""
            return true
        }
        return false
    }

    ScrollView {
        id: viewport
        anchors.fill: parent
        anchors.margins: 16
        clip: true
        contentWidth: availableWidth

        Item {
            width: viewport.availableWidth
            implicitHeight: dashboard.implicitHeight + 8

            ColumnLayout {
                id: dashboard
                width: Math.min(parent.width, 1540)
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 16

                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: 4
                    Layout.rightMargin: 4
                    Label {
                        text: "Settings"
                        font.pixelSize: 30
                        font.bold: true
                        Layout.fillWidth: true
                    }
                    Label {
                        text: "Audio, Discord and keyboard controls"
                        color: "#8298ab"
                        font.pixelSize: 15
                    }
                }

                GridLayout {
                    id: topGrid
                    Layout.fillWidth: true
                    columns: width >= 1020 ? 2 : 1
                    columnSpacing: 16
                    rowSpacing: 16

                    Rectangle {
                        objectName: "localAudioSettingsCard"
                        Layout.row: 0
                        Layout.column: 0
                        Layout.fillWidth: true
                        Layout.minimumWidth: 390
                        Layout.preferredHeight: 380
                        Layout.alignment: Qt.AlignTop
                        radius: 10
                        color: "#141d26"
                        border.color: "#34495c"

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 12
                            Label {
                                text: "Local Audio"
                                font.pixelSize: 22
                                font.bold: true
                            }
                            Label {
                                text: "Choose the physical output used in the room."
                                color: "#8fa5b8"
                                font.pixelSize: 14
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Label {
                                text: "OUTPUT DEVICE"
                                color: "#a9bdcd"
                                font.pixelSize: 13
                                font.bold: true
                                Layout.topMargin: 4
                            }
                            ComboBox {
                                id: outputCombo
                                objectName: "settingsOutputCombo"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 52
                                font.pixelSize: 15
                                model: backendObject.availableOutputs
                                textRole: "label"
                                valueRole: "key"
                                Component.onCompleted: currentIndex = indexOfValue(backendObject.selectedOutput)
                                onModelChanged: currentIndex = indexOfValue(backendObject.selectedOutput)
                                onActivated: backendObject.selectOutput(currentValue)
                                popup: Popup {
                                    objectName: "audioOutputDevicePopup"
                                    y: outputCombo.height
                                    width: outputCombo.width
                                    implicitHeight: Math.min(deviceList.contentHeight + 2, 300)
                                    padding: 1
                                    background: Rectangle {
                                        color: "#243443"
                                        border.width: 1
                                        border.color: "#60798e"
                                        radius: 5
                                    }
                                    contentItem: ListView {
                                        id: deviceList
                                        clip: true
                                        interactive: true
                                        boundsBehavior: Flickable.StopAtBounds
                                        implicitHeight: contentHeight
                                        model: outputCombo.popup.visible
                                               ? outputCombo.delegateModel : null
                                        currentIndex: outputCombo.highlightedIndex
                                        ScrollBar.vertical: ScrollBar {
                                            policy: ScrollBar.AsNeeded
                                            interactive: true
                                            width: 20
                                        }
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Layout.topMargin: 4
                                Label {
                                    text: "RAM CACHE LIMIT"
                                    color: "#a9bdcd"
                                    font.pixelSize: 13
                                    font.bold: true
                                    Layout.fillWidth: true
                                }
                                Label {
                                    text: "64 MiB steps"
                                    color: "#71899d"
                                    font.pixelSize: 12
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                TouchButton {
                                    objectName: "decreaseAudioCacheButton"
                                    text: "−"
                                    Layout.preferredWidth: 62
                                    Layout.preferredHeight: 48
                                    font.pixelSize: 21
                                    enabled: backendObject.audioCacheMb > 64
                                    onClicked: backendObject.setAudioCacheMb(
                                        backendObject.audioCacheMb - 64)
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 48
                                    radius: 7
                                    color: "#101820"
                                    border.color: "#405466"
                                    Label {
                                        anchors.centerIn: parent
                                        text: backendObject.audioCacheMb + " MiB"
                                        color: "#e8eef5"
                                        font.pixelSize: 17
                                        font.bold: true
                                    }
                                }
                                TouchButton {
                                    objectName: "increaseAudioCacheButton"
                                    text: "+"
                                    Layout.preferredWidth: 62
                                    Layout.preferredHeight: 48
                                    font.pixelSize: 20
                                    enabled: backendObject.audioCacheMb < 2048
                                    onClicked: backendObject.setAudioCacheMb(
                                        backendObject.audioCacheMb + 64)
                                }
                            }
                            Item { Layout.fillHeight: true }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: backendObject.audioCacheText
                                    color: "#8298ab"
                                    font.pixelSize: 13
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                TouchButton {
                                    text: "Refresh Devices"
                                    Layout.preferredWidth: 172
                                    Layout.preferredHeight: root.actionHeight
                                    font.pixelSize: 15
                                    onClicked: backendObject.refreshOutputs()
                                }
                            }
                        }
                    }

                    Rectangle {
                        objectName: "discordSettingsCard"
                        Layout.row: topGrid.columns === 2 ? 0 : 1
                        Layout.column: topGrid.columns === 2 ? 1 : 0
                        Layout.fillWidth: true
                        Layout.minimumWidth: 540
                        Layout.preferredHeight: 430
                        Layout.alignment: Qt.AlignTop
                        radius: 10
                        color: "#141d26"
                        border.color: "#34495c"

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 9
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "Discord Output"
                                    font.pixelSize: 22
                                    font.bold: true
                                    Layout.fillWidth: true
                                }
                                Rectangle {
                                    Layout.preferredHeight: 32
                                    Layout.preferredWidth: Math.min(300, discordStatus.implicitWidth + 28)
                                    radius: 16
                                    color: backendObject.discordConnected ? "#1d4a42"
                                         : backendObject.discordState === "error" ? "#54303a"
                                         : "#253442"
                                    Label {
                                        id: discordStatus
                                        anchors.centerIn: parent
                                        text: backendObject.discordStatusText
                                        color: backendObject.discordConnected ? "#6ce5cd"
                                              : backendObject.discordState === "error" ? "#ff8997"
                                              : "#a9bdcd"
                                        font.pixelSize: 13
                                        font.bold: true
                                        elide: Text.ElideRight
                                        width: Math.min(implicitWidth, 272)
                                    }
                                }
                            }
                            Label { text: "BOT TOKEN"; color: "#a9bdcd"; font.pixelSize: 13; font.bold: true }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                TextField {
                                    id: tokenField
                                    objectName: "discordTokenField"
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 220
                                    Layout.preferredHeight: 52
                                    font.pixelSize: 15
                                    echoMode: TextInput.Password
                                    passwordCharacter: "•"
                                    placeholderText: backendObject.discordTokenConfigured
                                                     ? "Token configured — enter a replacement"
                                                     : "Paste Discord bot token"
                                    selectByMouse: true
                                }
                                TouchButton {
                                    objectName: "saveDiscordTokenButton"
                                    text: "Save"
                                    Layout.preferredWidth: 92
                                    Layout.preferredHeight: root.actionHeight
                                    font.pixelSize: 15
                                    enabled: tokenField.text.trim().length > 0
                                    onClicked: {
                                        if (backendObject.saveDiscordToken(tokenField.text))
                                            tokenField.clear()
                                    }
                                }
                                TouchButton {
                                    text: "Remove"
                                    Layout.preferredWidth: 104
                                    Layout.preferredHeight: root.actionHeight
                                    font.pixelSize: 15
                                    enabled: backendObject.discordTokenConfigured
                                    baseColor: "#743b48"
                                    onClicked: {
                                        tokenField.clear()
                                        backendObject.saveDiscordToken("")
                                    }
                                }
                            }
                            Label {
                                text: backendObject.discordTokenStatus.length > 0
                                      ? backendObject.discordTokenStatus
                                      : "Secure token file: " + backendObject.discordTokenLocation
                                color: "#71899d"
                                font.pixelSize: 12
                                elide: Text.ElideMiddle
                                Layout.fillWidth: true
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 5
                                    Label { text: "SERVER"; color: "#a9bdcd"; font.pixelSize: 13; font.bold: true }
                                    ComboBox {
                                        id: discordGuildCombo
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 50
                                        font.pixelSize: 15
                                        model: backendObject.discordGuilds
                                        textRole: "name"
                                        valueRole: "id"
                                        enabled: count > 0
                                        Component.onCompleted: currentIndex = indexOfValue(backendObject.selectedDiscordGuild)
                                        onModelChanged: currentIndex = indexOfValue(backendObject.selectedDiscordGuild)
                                        onActivated: backendObject.selectDiscordGuild(currentValue)
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 5
                                    Label { text: "VOICE CHANNEL"; color: "#a9bdcd"; font.pixelSize: 13; font.bold: true }
                                    ComboBox {
                                        id: discordChannelCombo
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 50
                                        font.pixelSize: 15
                                        model: backendObject.discordChannels
                                        textRole: "name"
                                        valueRole: "id"
                                        enabled: count > 0
                                    }
                                }
                            }
                            Item { Layout.fillHeight: true }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                TouchButton {
                                    objectName: "settingsDiscordAddBotButton"
                                    text: "Add Bot to Server"
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: root.actionHeight
                                    font.pixelSize: 15
                                    font.bold: true
                                    enabled: backendObject.discordCanInvite
                                    onClicked: backendObject.addDiscordBotToServer()
                                }
                                TouchButton {
                                    objectName: "settingsDiscordJoinButton"
                                    text: "Join Channel"
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: root.actionHeight
                                    font.pixelSize: 15
                                    font.bold: true
                                    enabled: backendObject.discordCanJoin
                                             && discordChannelCombo.currentIndex >= 0
                                    baseColor: "#2d6359"
                                    onClicked: backendObject.joinDiscordChannel(
                                        discordGuildCombo.currentValue,
                                        discordChannelCombo.currentValue)
                                }
                                TouchButton {
                                    objectName: "settingsDiscordLeaveButton"
                                    text: "Leave Channel"
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: root.actionHeight
                                    font.pixelSize: 15
                                    font.bold: true
                                    enabled: backendObject.discordConnected
                                    baseColor: "#743b48"
                                    onClicked: backendObject.leaveDiscordChannel()
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    objectName: "keyboardSettingsCard"
                    Layout.fillWidth: true
                    Layout.preferredHeight: keyboardContent.implicitHeight + 44
                    radius: 10
                    color: "#141d26"
                    border.color: "#34495c"

                    ColumnLayout {
                        id: keyboardContent
                        objectName: "keyboardSettingsContent"
                        anchors.fill: parent
                        anchors.margins: 22
                        spacing: 12
                        RowLayout {
                            Layout.fillWidth: true
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3
                                Label {
                                    text: "Keyboard Controls"
                                    font.pixelSize: 22
                                    font.bold: true
                                }
                                Label {
                                    text: "Tap a binding and press a new combination. Backspace clears it; Escape cancels."
                                    color: "#8298ab"
                                    font.pixelSize: 14
                                }
                            }
                            TouchButton {
                                text: "Reset All"
                                Layout.preferredWidth: 112
                                Layout.preferredHeight: 48
                                font.pixelSize: 15
                                onClicked: {
                                    root.capturingAction = ""
                                    backendObject.resetKeybindings()
                                }
                            }
                        }
                        GridLayout {
                            Layout.fillWidth: true
                            columns: width >= 920 ? 2 : 1
                            columnSpacing: 12
                            rowSpacing: 8
                            Repeater {
                                model: backendObject.keybindings
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 54
                                    radius: 7
                                    color: "#101820"
                                    border.color: root.capturingAction === modelData.id
                                                  ? "#55e0c4" : "#263a4b"
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 14
                                        anchors.rightMargin: 6
                                        spacing: 10
                                        Label {
                                            text: modelData.label
                                            Layout.fillWidth: true
                                            color: "#dce7ef"
                                            font.pixelSize: 15
                                            font.bold: true
                                        }
                                        TouchButton {
                                            id: bindingButton
                                            objectName: "keybinding-" + modelData.id
                                            text: root.capturingAction === modelData.id
                                                  ? "Press keys…"
                                                  : modelData.sequence.length > 0
                                                    ? modelData.sequence : "Unassigned"
                                            Layout.preferredWidth: 176
                                            Layout.preferredHeight: 42
                                            font.pixelSize: 15
                                            activeFocusOnTab: true
                                            checked: root.capturingAction === modelData.id
                                            onClicked: {
                                                root.capturingAction = modelData.id
                                                forceActiveFocus()
                                            }
                                            Keys.onPressed: function(event) {
                                                // Saving emits `changed` and rebuilds this
                                                // delegate. Retain the long-lived view before
                                                // calling the backend so the rest of this
                                                // handler never resolves through a dead context.
                                                let settingsView = root
                                                let actionId = modelData.id
                                                if (settingsView.capturingAction !== actionId)
                                                    return
                                                event.accepted = true
                                                if (event.key === Qt.Key_Escape) {
                                                    settingsView.capturingAction = ""
                                                    return
                                                }
                                                if (event.key === Qt.Key_Backspace
                                                        || event.key === Qt.Key_Delete) {
                                                    settingsView.commitCapturedBinding(
                                                        actionId, "")
                                                    return
                                                }
                                                let sequence = settingsView.sequenceForEvent(event)
                                                if (sequence.length > 0)
                                                    settingsView.commitCapturedBinding(
                                                        actionId, sequence)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    objectName: "diagnosticsSettingsCard"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 190
                    radius: 10
                    color: "#141d26"
                    border.color: "#34495c"

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 22
                        spacing: 18
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Label {
                                text: "Diagnostics & Logs"
                                font.pixelSize: 22
                                font.bold: true
                            }
                            Label {
                                text: "Rotating application logs are written outside the portable music library. Diagnostic mode lasts for this session only."
                                color: "#8298ab"
                                font.pixelSize: 14
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Label {
                                objectName: "logDirectoryLabel"
                                text: backendObject.logDirectory
                                color: "#71899d"
                                font.pixelSize: 12
                                elide: Text.ElideMiddle
                                Layout.fillWidth: true
                            }
                            Label {
                                visible: backendObject.diagnosticStatus.length > 0
                                text: backendObject.diagnosticStatus
                                color: "#55e0c4"
                                font.pixelSize: 13
                                Layout.fillWidth: true
                            }
                        }
                        TouchButton {
                            objectName: "diagnosticLoggingButton"
                            text: backendObject.diagnosticLogging
                                  ? "Diagnostic: ON" : "Diagnostic: OFF"
                            checkable: true
                            checked: backendObject.diagnosticLogging
                            Layout.preferredWidth: 176
                            Layout.preferredHeight: root.actionHeight
                            baseColor: "#31465a"
                            activeColor: "#2d7568"
                            onClicked: backendObject.setDiagnosticLogging(checked)
                        }
                        TouchButton {
                            objectName: "copyDiagnosticSummaryButton"
                            text: "Copy Summary"
                            Layout.preferredWidth: 154
                            Layout.preferredHeight: root.actionHeight
                            onClicked: backendObject.copyDiagnosticSummary()
                        }
                        TouchButton {
                            objectName: "openLogFolderButton"
                            text: "Open Log Folder"
                            Layout.preferredWidth: 170
                            Layout.preferredHeight: root.actionHeight
                            onClicked: backendObject.openLogFolder()
                        }
                    }
                }

                Label {
                    visible: backendObject.errorMessage.length > 0
                    text: backendObject.errorMessage
                    color: "#ff8190"
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    Layout.leftMargin: 4
                }
                Item { Layout.preferredHeight: 8 }
            }
        }
    }
}
