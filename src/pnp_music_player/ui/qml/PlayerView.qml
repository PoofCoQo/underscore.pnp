import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

Item {
    id: root
    objectName: "playerView"
    required property var backend
    // Keep a value reference instead of a live binding to Main. During QML
    // teardown the parent binding is cleared before all child bindings stop.
    property var backendObject: backend
    signal openSetup()
    signal openTrackSetup(string trackKey)
    signal relinkTrack(string trackKey)
    signal showMetadataProblems()
    readonly property int actionButtonHeight: 58
    readonly property bool discordHasToken: backendObject.discordTokenConfigured
    readonly property bool discordFaderActive: backendObject.discordConnected
    readonly property bool discordFaderAvailable: backendObject.discordFaderAvailable
    readonly property bool discordFaderBusy:
        backendObject.discordState === "connecting"
        || backendObject.discordState === "joining_channel"
        || backendObject.discordState === "reconnecting"
    readonly property bool discordFaderError: backendObject.discordState === "error"

    function discordFaderColor() {
        if (discordFaderError)
            return "#a84f60"
        if (discordFaderBusy)
            return "#b88a3d"
        if (discordFaderActive)
            return "#3b8177"
        return "#536273"
    }

    function discordFaderStatusText() {
        if (!discordHasToken)
            return "NO TOKEN"
        if (discordFaderError)
            return "ERROR"
        if (backendObject.discordState === "connecting")
            return "CONNECTING"
        if (backendObject.discordState === "joining_channel")
            return "JOINING"
        if (backendObject.discordState === "reconnecting")
            return "RECONNECTING"
        if (backendObject.discordState === "offline")
            return "OFFLINE"
        if (!discordFaderActive)
            return "STANDBY"
        if (backendObject.discordMuted)
            return "MUTE"
        return backendObject.discordDb <= -79.9
               ? "−∞" : backendObject.discordDb.toFixed(1) + " dB"
    }

    function meterPosition(db) {
        return Math.max(0.0, Math.min(1.0, (db + 80.0) / 80.0))
    }

    function meterColor(db) {
        if (db > -3.0)
            return "#ff6578"
        if (db > -12.0)
            return "#ffca64"
        return "#55e0c4"
    }

    Component.onCompleted: backendObject = backend

    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 12

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.fillHeight: true
            spacing: 10

            Rectangle {
                objectName: "metadataProblemsBanner"
                visible: backendObject.metadataProblems.length > 0
                Layout.fillWidth: true
                Layout.preferredHeight: 56
                radius: 8
                color: "#683c46"
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    Label {
                        Layout.fillWidth: true
                        text: backendObject.metadataProblems.length
                              + " metadata file(s) could not be loaded"
                        font.bold: true
                    }
                    TouchButton {
                        text: "Details"
                        onClicked: root.showMetadataProblems()
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 262
                radius: 10
                color: "#141d26"
                border.color: "#34495c"
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 24
                    ColumnLayout {
                        Layout.fillWidth: true
                        Label { text: "NOW PLAYING"; color: "#68d9ca"; font.bold: true; font.pixelSize: 15 }
                        Label { text: backendObject.currentTitle; font.bold: true; font.pixelSize: 27; elide: Text.ElideRight; Layout.fillWidth: true }
                        Label { text: backendObject.currentDetails; color: "#9eb0c2"; font.pixelSize: 15; elide: Text.ElideRight; Layout.fillWidth: true }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Label {
                                text: backendObject.playbackState
                                color: "#ffca64"
                                font.bold: true
                                font.pixelSize: 16
                                Layout.preferredWidth: 190
                                elide: Text.ElideRight
                            }
                            Label {
                                visible: backendObject.playing
                                text: backendObject.nextJumpText + " left"
                                color: "#d3deE8"
                                font.bold: true
                                font.pixelSize: 15
                                Layout.preferredWidth: 102
                            }
                            Label {
                                visible: backendObject.playing
                                text: "Loop " + (backendObject.loopCount + 1)
                                color: "#a9bacb"
                                Layout.preferredWidth: 62
                            }
                            Label {
                                visible: backendObject.playing
                                         && backendObject.hasNextExit
                                text: "Exit in " + backendObject.nextExitText
                                color: "#ffca64"
                                font.bold: true
                                font.pixelSize: 15
                                Layout.preferredWidth: 132
                            }
                            Item { Layout.fillWidth: true }
                        }
                        RowLayout {
                            visible: backendObject.currentLoops.length > 0
                            Layout.fillWidth: true
                            Layout.preferredHeight: 44
                            Layout.bottomMargin: 8
                            spacing: 8
                            Label {
                                text: backendObject.pendingLoopName.length > 0 ? "NEXT LOOP" : "LOOPS"
                                color: backendObject.pendingLoopName.length > 0 ? "#ffca64" : "#70e2c5"
                                font.bold: true
                                font.pixelSize: 13
                                Layout.preferredWidth: 112
                                elide: Text.ElideRight
                            }
                            ListView {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 44
                                orientation: ListView.Horizontal
                                spacing: 7; clip: true
                                model: backendObject.currentLoops
                                delegate: TouchButton {
                                    required property var modelData
                                    text: modelData.name + (modelData.pending ? "  →" : "")
                                    width: 150
                                    height: 44
                                    checkable: true
                                    checked: modelData.active || modelData.pending
                                    activeColor: modelData.pending
                                                 ? "#ffca64" : "#55e0c4"
                                    enabled: backendObject.playing
                                             && !backendObject.transitionActive
                                    onClicked: backendObject.selectLoop(modelData.id)
                                }
                                ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                            }
                        }
                    }
                    Rectangle { width: 1; Layout.fillHeight: true; color: "#34495c" }
                    ColumnLayout {
                        Layout.preferredWidth: 410
                        Label { text: "NEXT"; color: "#ffca64"; font.bold: true; font.pixelSize: 15 }
                        Label { text: backendObject.queuedTitle; font.bold: true; font.pixelSize: 20; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        Label {
                            text: backendObject.queuedTitle === "—"
                                  ? "Tap a track card to set NEXT"
                                  : backendObject.canCancelQueue
                                    ? "Tap again to remove it from NEXT"
                                    : "Waiting for transition"
                            color: "#91a5b9"; wrapMode: Text.Wrap; Layout.fillWidth: true
                        }
                        Label {
                            visible: backendObject.queuedParts.length > 0
                            text: "START SECTION"
                            color: "#ffca64"
                            font.bold: true
                            font.pixelSize: 13
                        }
                        GridView {
                            id: queuedPartsGrid
                            objectName: "queuedPartsGrid"
                            visible: backendObject.queuedParts.length > 0
                            Layout.fillWidth: true
                            Layout.preferredHeight: 96
                            flow: GridView.FlowTopToBottom
                            cellWidth: 146
                            cellHeight: 46
                            clip: true
                            model: backendObject.queuedParts
                            delegate: TouchButton {
                                required property var modelData
                                width: 138
                                height: 40
                                text: modelData.name + (modelData.selected ? "  →" : "")
                                checkable: true
                                checked: modelData.selected
                                activeColor: "#ffca64"
                                enabled: backendObject.canSelectQueuedPart
                                onClicked: backendObject.selectQueuedEntry(modelData.id)
                            }
                            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                spacing: 8
                Label {
                    text: "CATEGORY"
                    color: "#9eb0c2"
                    font.bold: true
                    Layout.preferredWidth: 92
                }
                ListView {
                    id: categoryList
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    orientation: ListView.Horizontal
                    spacing: 8
                    clip: true
                    model: backendObject.categories
                    delegate: TouchButton {
                        required property string modelData
                        text: modelData
                        checkable: true
                        checked: backendObject.currentCategory === modelData
                        onClicked: backendObject.selectCategory(modelData)
                    }
                    ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                spacing: 8
                Label {
                    text: "ALBUM"
                    color: "#9eb0c2"
                    font.bold: true
                    Layout.preferredWidth: 92
                }
                ListView {
                    id: albumList
                    objectName: "albumList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    orientation: ListView.Horizontal
                    spacing: 8
                    clip: true
                    model: backendObject.albums
                    delegate: TouchButton {
                        required property string modelData
                        text: modelData
                        checkable: true
                        checked: backendObject.currentAlbum === modelData
                        onClicked: backendObject.selectAlbum(modelData)
                    }
                    ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 8
                color: "#0f161e"
                border.color: "#283746"

                GridView {
                    id: trackGrid
                    anchors.fill: parent
                    anchors.margins: 10
                    clip: true
                    cellWidth: 238
                    cellHeight: 146
                    model: backendObject.tracks
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    delegate: Item {
                        required property string trackKey
                        required property string title
                        required property string displayName
                        required property string fileName
                        required property string artist
                        required property string category
                        required property string tags
                        required property bool isPlaying
                        required property bool isQueued
                        required property bool isValid
                        required property bool isMissingAudio
                        required property bool isFavorite
                        required property string statusLabel
                        width: trackGrid.cellWidth
                        height: trackGrid.cellHeight
                        Rectangle {
                            anchors.fill: parent
                            anchors.margins: 5
                            radius: 10
                            color: !isValid ? "#292d33" : isPlaying ? "#173b38" : isQueued ? "#493d1e" : cardTap.pressed ? "#30465a" : "#1b2834"
                            border.width: isPlaying || isQueued ? 3 : 1
                            border.color: !isValid ? "#747b84" : isPlaying ? "#55e0c4" : isQueued ? "#ffca64" : "#405466"
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 12
                                anchors.rightMargin: 58
                                spacing: 4
                                Label { visible: statusLabel.length > 0; text: statusLabel; color: isPlaying ? "#6ff0d8" : isQueued ? "#ffda81" : "#c1c6cc"; font.bold: true }
                                Label { text: displayName; font.pixelSize: 17; font.bold: true; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; Layout.fillWidth: true }
                                Label { text: fileName; color: "#7f93a7"; font.pixelSize: 12; elide: Text.ElideMiddle; Layout.fillWidth: true }
                                Label { visible: artist.length > 0; text: artist; color: "#a6b5c5"; elide: Text.ElideRight; Layout.fillWidth: true }
                                Label { text: category + (tags.length ? " · " + tags : ""); color: "#8ea2b5"; elide: Text.ElideRight; Layout.fillWidth: true }
                            }
                            MouseArea {
                                id: cardTap
                                anchors.fill: parent
                                enabled: isValid && !backendObject.loading
                                onClicked: backendObject.selectTrack(trackKey)
                            }
                            TouchButton {
                                objectName: "favoriteButton"
                                anchors.top: parent.top
                                anchors.right: parent.right
                                anchors.topMargin: 0
                                anchors.rightMargin: 0
                                width: 44
                                height: 44
                                z: 2
                                text: isFavorite ? "★" : "☆"
                                font.pixelSize: 25
                                baseColor: "#263746"
                                foregroundColor: isFavorite ? "#ffca64" : "#b8c6d3"
                                Accessible.name: isFavorite
                                                 ? "Remove from favorites"
                                                 : "Add to favorites"
                                onClicked: backendObject.toggleFavorite(trackKey)
                            }
                            TouchButton {
                                objectName: "trackSetupButton"
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.rightMargin: 0
                                anchors.bottomMargin: 0
                                width: 44
                                height: 44
                                z: 2
                                text: isMissingAudio ? "⌕" : "✎"
                                font.pixelSize: 23
                                baseColor: "#263746"
                                foregroundColor: "#b8c6d3"
                                enabled: !backendObject.loading
                                Accessible.name: isMissingAudio ? "Locate audio file" : "Open track in Setup"
                                onClicked: {
                                    if (isMissingAudio)
                                        root.relinkTrack(trackKey)
                                    else
                                        root.openTrackSetup(trackKey)
                                }
                            }
                        }
                    }
                }

                ColumnLayout {
                    visible: backendObject.libraryEmpty
                    anchors.centerIn: parent
                    spacing: 16
                    Label { text: "No tracks in the library"; font.pixelSize: 24; font.bold: true; Layout.alignment: Qt.AlignHCenter }
                    Label { text: "Open Setup and import music from data/music."; color: "#9badbf"; Layout.alignment: Qt.AlignHCenter }
                    TouchButton { text: "Open Setup"; Layout.alignment: Qt.AlignHCenter; onClicked: root.openSetup() }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                ButtonGroup { id: transitionGroup }
                TouchButton { text: "Normal"; Layout.preferredHeight: root.actionButtonHeight; Layout.preferredWidth: 100; font.pixelSize: 16; font.bold: checked; checkable: true; ButtonGroup.group: transitionGroup; checked: backendObject.transitionMode === "normal"; onClicked: backendObject.setTransitionMode("normal") }
                TouchButton { text: "Cut"; Layout.preferredHeight: root.actionButtonHeight; Layout.preferredWidth: 80; font.pixelSize: 16; font.bold: checked; checkable: true; ButtonGroup.group: transitionGroup; checked: backendObject.transitionMode === "hard_cut"; onClicked: backendObject.setTransitionMode("hard_cut") }
                TouchButton { text: "Fade"; Layout.preferredHeight: root.actionButtonHeight; Layout.preferredWidth: 84; font.pixelSize: 16; font.bold: checked; checkable: true; ButtonGroup.group: transitionGroup; checked: backendObject.transitionMode === "fade"; onClicked: backendObject.setTransitionMode("fade") }
                TouchButton { text: "Crossfade"; Layout.preferredHeight: root.actionButtonHeight; Layout.preferredWidth: 116; font.pixelSize: 16; font.bold: checked; checkable: true; ButtonGroup.group: transitionGroup; checked: backendObject.transitionMode === "crossfade"; onClicked: backendObject.setTransitionMode("crossfade") }
                Item { width: 8 }
                TouchButton { text: "−"; Layout.preferredHeight: root.actionButtonHeight; Layout.preferredWidth: 54; font.pixelSize: 20; enabled: backendObject.transitionMode === "fade" || backendObject.transitionMode === "crossfade"; onClicked: backendObject.setTransitionDuration(backendObject.transitionDuration - 0.5) }
                Label { text: backendObject.transitionDuration.toFixed(1) + " s"; font.pixelSize: 17; horizontalAlignment: Text.AlignHCenter; Layout.preferredWidth: 58 }
                TouchButton { text: "+"; Layout.preferredHeight: root.actionButtonHeight; Layout.preferredWidth: 54; font.pixelSize: 20; enabled: backendObject.transitionMode === "fade" || backendObject.transitionMode === "crossfade"; onClicked: backendObject.setTransitionDuration(backendObject.transitionDuration + 0.5) }
                TouchButton { text: "GO NOW"; baseColor: "#2d6359"; activeColor: "#55e0c4"; enabled: backendObject.canGoNow; font.bold: true; font.pixelSize: 16; Layout.preferredHeight: root.actionButtonHeight; Layout.preferredWidth: 120; onClicked: backendObject.goNow() }
                Item { Layout.fillWidth: true }
                TouchButton { text: backendObject.paused ? "▶ Resume" : "Ⅱ Pause"; baseColor: backendObject.paused ? "#326b59" : "#755728"; activeColor: "#ffca64"; font.bold: true; font.pixelSize: 16; Layout.preferredHeight: root.actionButtonHeight; Layout.preferredWidth: 116; enabled: backendObject.playing; onClicked: backendObject.pauseResume() }
                TouchButton { objectName: "playerStopButton"; text: "■ Stop"; baseColor: "#743b48"; activeColor: "#ff7184"; font.bold: true; font.pixelSize: 16; Layout.preferredHeight: root.actionButtonHeight; Layout.preferredWidth: 96; enabled: backendObject.playing; onClicked: backendObject.stop() }
            }
        }

        Rectangle {
            id: outputControlsPanel
            objectName: "outputControlsPanel"
            Layout.preferredWidth: 286
            Layout.minimumWidth: 286
            Layout.maximumWidth: 286
            Layout.fillHeight: true
            radius: 10
            color: "#141d26"
            border.color: "#34495c"
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 8
                    ColumnLayout {
                        id: localOutputColumn
                        objectName: "localOutputColumn"
                        Layout.preferredWidth: 122
                        Layout.minimumWidth: 122
                        Layout.maximumWidth: 122
                        Layout.fillHeight: true
                        spacing: 8
                        Label { text: "LOCAL"; font.bold: true; font.pixelSize: 17; Layout.alignment: Qt.AlignHCenter }
                        Label {
                            text: backendObject.muted ? "MUTE" : (backendObject.masterDb <= -79.9 ? "−∞" : backendObject.masterDb.toFixed(1) + " dB")
                            color: backendObject.muted ? "#ff7f8f" : "#e8eef5"
                            font.bold: true; font.pixelSize: 17; Layout.alignment: Qt.AlignHCenter
                        }
                        Slider {
                            id: masterSlider
                            objectName: "masterSlider"
                            Layout.fillHeight: true
                            Layout.preferredWidth: 76
                            Layout.minimumWidth: 76
                            Layout.alignment: Qt.AlignHCenter
                            orientation: Qt.Vertical
                            from: -80; to: 0; stepSize: 0.5
                            value: backendObject.masterDb
                            live: true
                            onMoved: backendObject.setMasterDb(value)
                            onValueChanged: {
                                if (pressed && Math.abs(value - backendObject.masterDb) >= 0.01)
                                    backendObject.setMasterDb(value)
                            }
                            background: Item {
                                x: masterSlider.leftPadding + masterSlider.availableWidth / 2 - width / 2
                                y: masterSlider.topPadding
                                width: 54; height: masterSlider.availableHeight
                                Rectangle { x: 21; width: 12; height: parent.height; radius: 6; color: "#2c3c4b" }
                                Rectangle { x: 21; width: 12; anchors.bottom: parent.bottom; height: masterSlider.position * parent.height; radius: 6; color: "#357a70" }
                                Rectangle {
                                    x: 3; width: 7; radius: 3; anchors.bottom: parent.bottom
                                    height: root.meterPosition(backendObject.peakLeftDb) * parent.height
                                    color: root.meterColor(backendObject.peakLeftDb)
                                    Behavior on height { NumberAnimation { duration: 90 } }
                                }
                                Rectangle {
                                    x: 44; width: 7; radius: 3; anchors.bottom: parent.bottom
                                    height: root.meterPosition(backendObject.peakRightDb) * parent.height
                                    color: root.meterColor(backendObject.peakRightDb)
                                    Behavior on height { NumberAnimation { duration: 90 } }
                                }
                            }
                            handle: Rectangle {
                                x: masterSlider.leftPadding + masterSlider.availableWidth / 2 - width / 2
                                y: masterSlider.topPadding + masterSlider.visualPosition * (masterSlider.availableHeight - height)
                                width: 58; height: 34; radius: 8; color: masterSlider.pressed ? "#ffffff" : "#dce7ef"; border.color: "#17202a"; border.width: 2
                            }
                        }
                        TouchButton { objectName: "localMuteButton"; text: backendObject.muted ? "UNMUTE" : "MUTE"; baseColor: "#743b48"; activeColor: "#ff7184"; font.bold: true; font.pixelSize: 15; checkable: true; checked: backendObject.muted; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredHeight: 54; onClicked: backendObject.toggleMute() }
                    }
                    Rectangle { Layout.fillHeight: true; Layout.preferredWidth: 1; color: "#34495c" }
                    ColumnLayout {
                        id: discordOutputColumn
                        objectName: "discordOutputColumn"
                        Layout.preferredWidth: 122
                        Layout.minimumWidth: 122
                        Layout.maximumWidth: 122
                        Layout.fillHeight: true
                        spacing: 8
                        Label {
                            text: "DISCORD"
                            color: root.discordFaderError ? "#ff7f8f"
                                 : root.discordFaderBusy ? "#ffca64"
                                 : root.discordFaderActive ? "#55e0c4" : "#8797a7"
                            font.bold: true; font.pixelSize: 17
                            Layout.alignment: Qt.AlignHCenter
                        }
                        Label {
                            objectName: "discordFaderStatusLabel"
                            text: root.discordFaderStatusText()
                            color: root.discordFaderError ? "#ff7f8f"
                                 : root.discordFaderBusy ? "#ffca64"
                                 : backendObject.discordMuted && root.discordFaderActive
                                   ? "#ff7f8f"
                                   : root.discordFaderActive ? "#e8eef5" : "#78899a"
                            font.bold: true; font.pixelSize: 17; Layout.alignment: Qt.AlignHCenter
                        }
                        Slider {
                            id: discordSlider
                            objectName: "discordSlider"
                            Layout.fillHeight: true
                            Layout.preferredWidth: 76
                            Layout.minimumWidth: 76
                            Layout.alignment: Qt.AlignHCenter
                            orientation: Qt.Vertical
                            from: -80; to: 0; stepSize: 0.5
                            value: backendObject.discordDb
                            enabled: root.discordFaderAvailable
                            live: true
                            onMoved: backendObject.setDiscordDb(value)
                            onValueChanged: {
                                if (pressed && Math.abs(value - backendObject.discordDb) >= 0.01)
                                    backendObject.setDiscordDb(value)
                            }
                            background: Item {
                                x: discordSlider.leftPadding + discordSlider.availableWidth / 2 - width / 2
                                y: discordSlider.topPadding
                                width: 54; height: discordSlider.availableHeight
                                Rectangle {
                                    objectName: "discordFaderTrack"
                                    x: 21; width: 12; height: parent.height; radius: 6
                                    color: root.discordFaderAvailable ? "#2c3c4b" : "#46515d"
                                }
                                Rectangle {
                                    x: 21; width: 12; anchors.bottom: parent.bottom
                                    height: root.discordFaderAvailable
                                            ? discordSlider.position * parent.height : 0
                                    radius: 6
                                    color: root.discordFaderColor()
                                    opacity: root.discordFaderActive ? 1.0 : 0.48
                                }
                                Rectangle {
                                    x: 3; width: 7; radius: 3; anchors.bottom: parent.bottom
                                    height: root.discordFaderAvailable
                                            ? root.meterPosition(backendObject.discordPeakLeftDb) * parent.height
                                            : 0
                                    color: root.meterColor(backendObject.discordPeakLeftDb)
                                    Behavior on height { NumberAnimation { duration: 90 } }
                                }
                                Rectangle {
                                    x: 44; width: 7; radius: 3; anchors.bottom: parent.bottom
                                    height: root.discordFaderAvailable
                                            ? root.meterPosition(backendObject.discordPeakRightDb) * parent.height
                                            : 0
                                    color: root.meterColor(backendObject.discordPeakRightDb)
                                    Behavior on height { NumberAnimation { duration: 90 } }
                                }
                            }
                            handle: Rectangle {
                                objectName: "discordFaderHandle"
                                visible: root.discordFaderAvailable
                                x: discordSlider.leftPadding + discordSlider.availableWidth / 2 - width / 2
                                y: discordSlider.topPadding + discordSlider.visualPosition * (discordSlider.availableHeight - height)
                                width: 58; height: 34; radius: 8
                                color: root.discordFaderActive
                                       ? (discordSlider.pressed ? "#ffffff" : "#dce7ef")
                                       : root.discordFaderBusy ? "#8a7655"
                                       : root.discordFaderError ? "#81505a" : "#5c6976"
                                border.color: "#17202a"; border.width: 2
                            }
                        }
                        TouchButton { objectName: "discordMuteButton"; text: backendObject.discordMuted ? "UNMUTE" : "MUTE"; baseColor: "#743b48"; activeColor: "#ff7184"; font.bold: true; font.pixelSize: 15; checkable: true; checked: backendObject.discordMuted; enabled: root.discordFaderActive; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredHeight: 54; onClicked: backendObject.toggleDiscordMute() }
                    }
                }
            }
        }
    }

    Popup {
        id: outputPopup
        objectName: "audioOutputPopup"
        anchors.centerIn: parent
        width: Math.min(560, root.width - 48)
        height: Math.min(650, root.height - 48)
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
            anchors.margins: 24
            spacing: 10

            Label {
                text: "Audio Output"
                font.pixelSize: 22
                font.bold: true
                Layout.fillWidth: true
            }
            Label {
                text: backendObject.audioCacheText
                color: "#9eb0c2"
                font.pixelSize: 14
                Layout.fillWidth: true
            }
            Label {
                text: "Output Device"
                color: "#b9c7d6"
                font.pixelSize: 14
                font.bold: true
                Layout.topMargin: 4
            }
            ComboBox {
                id: outputCombo
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                model: backendObject.availableOutputs
                textRole: "label"
                valueRole: "key"
                Component.onCompleted: currentIndex = indexOfValue(backendObject.selectedOutput)
                onActivated: backendObject.selectOutput(currentValue)

                popup: Popup {
                    objectName: "audioOutputDevicePopup"
                    y: outputCombo.height
                    width: outputCombo.width
                    implicitHeight: Math.min(deviceList.contentHeight + 2, 260)
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
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                Layout.topMargin: 8
                Layout.bottomMargin: 4
                color: "#405467"
            }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: "Discord Output"
                    font.pixelSize: 20
                    font.bold: true
                    Layout.fillWidth: true
                }
                Label {
                    text: backendObject.discordStatusText
                    color: backendObject.discordConnected ? "#55e0c4"
                          : backendObject.discordState === "error" ? "#ff7184"
                          : "#9eb0c2"
                    font.pixelSize: 13
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideRight
                    Layout.preferredWidth: 290
                }
            }
            Label {
                text: "Server"
                color: "#b9c7d6"
                font.pixelSize: 14
                font.bold: true
            }
            ComboBox {
                id: discordGuildCombo
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                model: backendObject.discordGuilds
                textRole: "name"
                valueRole: "id"
                enabled: count > 0
                Component.onCompleted: currentIndex = indexOfValue(backendObject.selectedDiscordGuild)
                onModelChanged: currentIndex = indexOfValue(backendObject.selectedDiscordGuild)
                onActivated: backendObject.selectDiscordGuild(currentValue)
            }
            Label {
                text: "Voice Channel"
                color: "#b9c7d6"
                font.pixelSize: 14
                font.bold: true
            }
            ComboBox {
                id: discordChannelCombo
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                model: backendObject.discordChannels
                textRole: "name"
                valueRole: "id"
                enabled: count > 0
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: root.actionButtonHeight
                Layout.minimumHeight: root.actionButtonHeight
                Layout.maximumHeight: root.actionButtonHeight
                spacing: 10
                TouchButton {
                    objectName: "discordAddBotButton"
                    text: "Add Bot to Server"
                    Layout.preferredWidth: 174
                    Layout.preferredHeight: root.actionButtonHeight
                    font.pixelSize: 16
                    font.bold: true
                    enabled: backendObject.discordCanInvite
                    onClicked: backendObject.addDiscordBotToServer()
                }
                TouchButton {
                    objectName: "discordJoinButton"
                    text: "Join Channel"
                    Layout.preferredWidth: 142
                    Layout.preferredHeight: root.actionButtonHeight
                    font.pixelSize: 16
                    font.bold: true
                    enabled: backendObject.discordCanJoin
                             && discordChannelCombo.currentIndex >= 0
                    baseColor: "#2d6359"
                    activeColor: "#55e0c4"
                    onClicked: backendObject.joinDiscordChannel(
                        discordGuildCombo.currentValue, discordChannelCombo.currentValue)
                }
                TouchButton {
                    objectName: "discordLeaveButton"
                    text: "Leave Channel"
                    Layout.preferredWidth: 142
                    Layout.preferredHeight: root.actionButtonHeight
                    font.pixelSize: 16
                    font.bold: true
                    enabled: backendObject.discordConnected
                    baseColor: "#743b48"
                    activeColor: "#ff7184"
                    onClicked: backendObject.leaveDiscordChannel()
                }
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: root.actionButtonHeight
                Layout.minimumHeight: root.actionButtonHeight
                Layout.maximumHeight: root.actionButtonHeight
                TouchButton {
                    text: "Refresh Devices"
                    Layout.preferredWidth: 164
                    Layout.preferredHeight: root.actionButtonHeight
                    font.pixelSize: 16
                    onClicked: backendObject.refreshOutputs()
                }
                Item { Layout.fillWidth: true }
                TouchButton {
                    text: "Close"
                    Layout.preferredWidth: 104
                    Layout.preferredHeight: root.actionButtonHeight
                    font.pixelSize: 16
                    onClicked: outputPopup.close()
                }
            }
        }
    }

    Rectangle {
        visible: backendObject.loading
        anchors.centerIn: parent
        width: 280; height: 90; radius: 10; color: "#e6222d38"; z: 100
        RowLayout {
            anchors.centerIn: parent
            BusyIndicator { running: true }
            Label { text: "Loading track…"; font.pixelSize: 17 }
        }
    }
}
