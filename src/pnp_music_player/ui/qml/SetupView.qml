import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UnderscorePnP 1.0
import "components"

Item {
    id: root
    objectName: "setupView"
    required property var backend
    signal trackArchived()
    property int editorTab: 0
    property string selectedArea: "loop"
    property real pinchPreviousScale: 1.0
    property real dragPreviousX: 0.0

    readonly property bool waveformTab: editorTab === 0 || editorTab === 1
    readonly property int selectedStart: selectedArea === "play" ? backend.playStart
        : selectedArea === "intro" ? backend.introStart
        : selectedArea === "outro" ? backend.outroStart : backend.selectedLoopStart
    readonly property int selectedEnd: selectedArea === "play" ? backend.playEnd
        : selectedArea === "intro" ? backend.introEnd
        : selectedArea === "outro" ? backend.outroEnd : backend.selectedLoopEnd
    readonly property string selectedStartMarker: selectedArea === "play" ? "play_start"
        : selectedArea === "intro" ? "intro_start"
        : selectedArea === "outro" ? "outro_start" : "selected_loop_start"
    readonly property string selectedEndMarker: selectedArea === "play" ? "play_end"
        : selectedArea === "intro" ? "intro_end"
        : selectedArea === "outro" ? "outro_end" : "selected_loop_end"
    readonly property color selectedColor: selectedArea === "play" ? "#69d2ff"
        : selectedArea === "intro" ? "#70bfff"
        : selectedArea === "outro" ? "#ff7f8f" : "#72e59b"

    function setSelectedBoundary(which, value) {
        if (selectedArea === "loop")
            backend.setSelectedLoopBoundary(which, value)
        else
            backend.setMarker(selectedArea + "_" + which, value)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Label {
                text: backend.hasTrack ? backend.displayName : "No audio file open"
                font.pixelSize: 23; font.bold: true
                Layout.fillWidth: true; elide: Text.ElideMiddle
            }
            Label { visible: backend.dirty; text: "UNSAVED"; color: "#ffca64"; font.bold: true }
            TouchButton { visible: root.waveformTab; text: "−"; onClicked: backend.zoom(0.7, (backend.visibleStart + backend.visibleEnd) / 2) }
            TouchButton { visible: root.waveformTab; text: "+"; onClicked: backend.zoom(1.4, (backend.visibleStart + backend.visibleEnd) / 2) }
            TouchButton { visible: root.waveformTab; text: "Fit"; onClicked: backend.fit() }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            ButtonGroup { id: editorTabs }
            Repeater {
                model: ["Sections", "Beat & Meter", "Track Details", "Loudness"]
                TouchButton {
                    required property int index
                    required property string modelData
                    text: modelData
                    checkable: true
                    checked: root.editorTab === index
                    ButtonGroup.group: editorTabs
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    font.pixelSize: 16
                    font.bold: checked
                    onClicked: root.editorTab = index
                }
            }
        }

        Rectangle {
            id: waveFrame
            objectName: "waveFrame"
            visible: root.waveformTab
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 300
            color: "#11161d"; border.color: "#344352"; radius: 8; clip: true

            WaveformItem {
                id: waveform
                objectName: "waveform"
                anchors.fill: parent
                anchors.leftMargin: 54; anchors.rightMargin: 54
                anchors.topMargin: root.editorTab === 0 ? 104 : 42
                anchors.bottomMargin: 32
                backend: root.backend
            }

            MouseArea {
                id: navigation
                anchors.fill: waveform
                acceptedButtons: Qt.LeftButton | Qt.MiddleButton
                property real lastX: 0
                onPressed: function(mouse) {
                    lastX = mouse.x
                    if (mouse.button === Qt.LeftButton)
                        backend.setCursor(backend.xToFrame(mouse.x, width))
                }
                onPositionChanged: function(mouse) {
                    if (pressedButtons & Qt.MiddleButton) {
                        backend.pan(Math.round((lastX - mouse.x) *
                            (backend.visibleEnd - backend.visibleStart) / width))
                        lastX = mouse.x
                    }
                }
                onWheel: function(wheel) {
                    backend.zoom(wheel.angleDelta.y > 0 ? 1.3 : 0.77,
                                 backend.xToFrame(wheel.x, width))
                    wheel.accepted = true
                }
            }
            PinchHandler {
                parent: waveform; target: null
                onActiveChanged: root.pinchPreviousScale = 1.0
                onActiveScaleChanged: {
                    if (!active || activeScale <= 0) return
                    let factor = activeScale / root.pinchPreviousScale
                    backend.zoom(factor, backend.xToFrame(centroid.position.x, waveform.width))
                    root.pinchPreviousScale = activeScale
                }
            }
            DragHandler {
                parent: waveform; target: null
                acceptedDevices: PointerDevice.TouchScreen
                onActiveChanged: root.dragPreviousX = 0
                onActiveTranslationChanged: {
                    let delta = activeTranslation.x - root.dragPreviousX
                    backend.pan(Math.round(-delta *
                        (backend.visibleEnd - backend.visibleStart) / waveform.width))
                    root.dragPreviousX = activeTranslation.x
                }
            }

            // Permanent ranges are compact bands. Only the selected range gets
            // the two large draggable handles above the waveform.
            TimelineBand {
                visible: root.editorTab === 0
                backend: root.backend; viewport: waveform; bandName: "play"; label: "PLAY"
                startFrame: backend.playStart; endFrame: backend.playEnd
                bandColor: "#69d2ff"; selected: root.selectedArea === "play"
                laneY: waveform.y + 4
                onTapped: root.selectedArea = "play"
            }
            TimelineBand {
                visible: root.editorTab === 0
                backend: root.backend; viewport: waveform; bandName: "intro"; label: "INTRO"
                startFrame: backend.introStart; endFrame: backend.introEnd
                bandColor: "#70bfff"; selected: root.selectedArea === "intro"
                laneY: waveform.y + 35
                onTapped: root.selectedArea = "intro"
            }
            Repeater {
                id: loopBandRepeater
                objectName: "loopBandRepeater"
                model: backend.loopRegions
                readonly property real firstLaneY: count > 0 ? itemAt(0).y : -1
                readonly property real secondLaneY: count > 1 ? itemAt(1).y : -1
                TimelineBand {
                    required property int index
                    required property var modelData
                    visible: root.editorTab === 0
                    backend: root.backend; viewport: waveform
                    bandName: "loop-" + modelData.id
                    label: modelData.name
                    startFrame: modelData.start; endFrame: modelData.end
                    bandColor: modelData.main ? "#72e59b" : "#a6e76f"
                    selected: root.selectedArea === "loop" && backend.selectedLoopId === modelData.id
                    laneY: waveform.y + 66 + index * 31
                    onTapped: { backend.selectLoop(modelData.id); root.selectedArea = "loop" }
                }
            }
            TimelineBand {
                visible: root.editorTab === 0
                backend: root.backend; viewport: waveform; bandName: "outro"; label: "OUTRO"
                startFrame: backend.outroStart; endFrame: backend.outroEnd
                bandColor: "#ff7f8f"; selected: root.selectedArea === "outro"
                laneY: waveform.y + 66 + backend.loopRegions.length * 31
                onTapped: root.selectedArea = "outro"
            }

            Repeater {
                id: loopExitRepeater
                objectName: "loopExitRepeater"
                model: backend.selectedLoopExitPoints
                readonly property real firstMarkerX: count > 0
                                                     ? itemAt(0).x : -1
                Item {
                    required property int modelData
                    objectName: "loopExitPoint"
                    property int dragFrame: modelData
                    visible: root.editorTab === 0
                             && root.selectedArea === "loop"
                             && dragFrame >= backend.visibleStart
                             && dragFrame <= backend.visibleEnd
                    x: {
                        // frameToX() is a method, so QML cannot discover its
                        // dependency on the viewport by itself. These reads
                        // keep the track-frame position tied to zoom and pan.
                        let visibleStart = backend.visibleStart
                        let visibleEnd = backend.visibleEnd
                        return waveform.x
                               + backend.frameToX(dragFrame, waveform.width) - 1
                    }
                    y: waveform.y
                    width: 3
                    height: waveform.height
                    z: 14
                    Rectangle {
                        anchors.fill: parent
                        color: "#ffca64"
                    }
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.top
                        anchors.bottomMargin: 3
                        width: 62
                        height: 28
                        radius: 6
                        color: exitDrag.pressed ? "#ffe09a" : "#ffca64"
                        Label {
                            anchors.centerIn: parent
                            text: "EXIT"
                            color: "#1a1608"
                            font.bold: true
                            font.pixelSize: 11
                        }
                    }
                    MouseArea {
                        id: exitDrag
                        x: (parent.width - width) / 2
                        y: -31
                        width: 62
                        height: parent.height + 31
                        cursorShape: Qt.SizeHorCursor
                        preventStealing: true
                        onPressed: function(mouse) {
                            updateFrame(mouse)
                        }
                        onPositionChanged: function(mouse) {
                            if (pressed)
                                updateFrame(mouse)
                        }
                        onReleased: backend.moveSelectedLoopExitPoint(
                            modelData, dragFrame
                        )
                        function updateFrame(mouse) {
                            let point = exitDrag.mapToItem(
                                waveform, mouse.x, mouse.y
                            )
                            dragFrame = backend.snapSelectedLoopFrame(
                                backend.xToFrame(point.x, waveform.width)
                            )
                        }
                    }
                }
            }

            TimelineMarker {
                id: selectedStartHandle
                visible: root.editorTab === 0
                backend: root.backend; viewport: waveform; aboveWaveform: true; lane: 0
                markerName: root.selectedStartMarker; label: "START"
                frame: root.selectedStart; markerColor: root.selectedColor
            }
            TimelineMarker {
                id: selectedEndHandle
                visible: root.editorTab === 0
                backend: root.backend; viewport: waveform; aboveWaveform: true; lane: 1
                markerName: root.selectedEndMarker; label: "END"
                frame: root.selectedEnd; markerColor: root.selectedColor
            }

            Rectangle {
                x: {
                    let visibleStart = backend.visibleStart
                    let visibleEnd = backend.visibleEnd
                    return waveform.x
                           + backend.frameToX(backend.cursorFrame, waveform.width) - 1
                }
                y: waveform.y; width: 2; height: waveform.height
                color: "#ffffff"; opacity: 0.68; z: 15
            }
            Rectangle {
                visible: backend.previewMode !== "stopped"
                x: {
                    let visibleStart = backend.visibleStart
                    let visibleEnd = backend.visibleEnd
                    return waveform.x
                           + backend.frameToX(backend.playheadFrame, waveform.width) - 1
                }
                y: waveform.y; width: 3; height: waveform.height
                color: "#f353ff"; z: 16
            }
            Label { anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 8; text: backend.timeText(backend.visibleStart); color: "#9badbf" }
            Label { anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 8; text: backend.timeText(backend.visibleEnd); color: "#9badbf" }
        }

        RowLayout {
            visible: root.waveformTab
            Layout.fillWidth: true; spacing: 8
            Label { text: "Snap"; color: "#b9c7d6" }
            TouchButton { text: "Off"; checkable: true; checked: backend.snapMode === "off"; onClicked: backend.setSnapMode("off") }
            TouchButton { text: "Beat"; enabled: backend.hasBeatGrid; checkable: true; checked: backend.snapMode === "beat"; onClicked: backend.setSnapMode("beat") }
            TouchButton { text: "Bar"; enabled: backend.hasBeatGrid; checkable: true; checked: backend.snapMode === "bar"; onClicked: backend.setSnapMode("bar") }
            Item { Layout.fillWidth: true }
            Label { text: "Cursor: " + backend.timeText(backend.cursorFrame); color: "#cbd8e5" }
        }

        RowLayout {
            visible: root.waveformTab
            Layout.fillWidth: true; spacing: 8
            TouchButton { text: "▶ Cursor"; enabled: backend.previewAvailable; onClicked: backend.previewRange("cursor", root.selectedStart, root.selectedEnd) }
            TouchButton { text: "▶ Section"; enabled: backend.previewAvailable; onClicked: backend.previewRange("once", root.selectedStart, root.selectedEnd) }
            TouchButton { text: "↻ Loop"; enabled: backend.previewAvailable; onClicked: backend.previewRange("loop", root.selectedStart, root.selectedEnd) }
            TouchButton { text: "⇥|⇤ Boundary"; enabled: backend.previewAvailable; onClicked: backend.previewRange("boundary", root.selectedStart, root.selectedEnd) }
            Item { Layout.fillWidth: true }
            TouchButton { text: "⏯ Pause"; enabled: backend.previewAvailable; onClicked: backend.pauseResume() }
            TouchButton { text: "■ Stop"; enabled: backend.previewAvailable; onClicked: backend.stop() }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: root.waveformTab ? 220 : 0
            Layout.fillHeight: !root.waveformTab
            currentIndex: root.editorTab

            // RANGES
            RowLayout {
                spacing: 12
                ScrollView {
                    Layout.preferredWidth: 470; Layout.fillHeight: true; clip: true
                    Flow {
                        width: parent.width; spacing: 8
                        TouchButton { text: "PLAY"; checkable: true; checked: root.selectedArea === "play"; onClicked: root.selectedArea = "play" }
                        TouchButton { text: "INTRO"; checkable: true; checked: root.selectedArea === "intro"; onClicked: root.selectedArea = "intro" }
                        Repeater {
                            model: backend.loopRegions
                            TouchButton {
                                required property var modelData
                                text: modelData.name
                                checkable: true
                                checked: root.selectedArea === "loop" && backend.selectedLoopId === modelData.id
                                onClicked: { backend.selectLoop(modelData.id); root.selectedArea = "loop" }
                            }
                        }
                        TouchButton { text: "+ Loop"; onClicked: { backend.addLoop(); root.selectedArea = "loop" } }
                        TouchButton { text: "OUTRO"; checkable: true; checked: root.selectedArea === "outro"; onClicked: root.selectedArea = "outro" }
                    }
                }
                Frame {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout {
                        anchors.fill: parent; spacing: 6
                        RowLayout {
                            Label { text: root.selectedArea === "loop" ? backend.selectedLoopName : root.selectedArea.toUpperCase(); font.pixelSize: 18; font.bold: true; Layout.fillWidth: true }
                            TouchButton { visible: root.selectedArea === "loop" && backend.selectedLoopId !== "main"; text: "Delete Loop"; onClicked: backend.removeSelectedLoop() }
                        }
                        RowLayout {
                            Label { text: "Start  " + backend.timeText(root.selectedStart); Layout.fillWidth: true }
                            TouchButton { text: "Start = Cursor"; onClicked: root.setSelectedBoundary("start", backend.cursorFrame) }
                            Label { text: "End  " + backend.timeText(root.selectedEnd); Layout.fillWidth: true }
                            TouchButton { text: "End = Cursor"; onClicked: root.setSelectedBoundary("end", backend.cursorFrame) }
                        }
                        ValueEditor {
                            visible: root.selectedArea === "loop"
                            title: "Name"; text: backend.selectedLoopName
                            onCommitted: value => backend.renameSelectedLoop(value)
                        }
                        RowLayout {
                            visible: root.selectedArea === "loop"
                            Layout.fillWidth: true
                            spacing: 7
                            Label { text: "Exit Points"; color: "#ffca64" }
                            TouchButton {
                                text: "+ Cursor"
                                enabled: backend.cursorFrame >= backend.selectedLoopStart
                                         && backend.cursorFrame <= backend.selectedLoopEnd
                                onClicked: backend.addSelectedLoopExitPoint(backend.cursorFrame)
                            }
                            TouchButton {
                                objectName: "addExitAtPlayheadButton"
                                text: "+ Playhead"
                                enabled: backend.previewMode !== "stopped"
                                         && backend.playheadFrame >= backend.selectedLoopStart
                                         && backend.playheadFrame <= backend.selectedLoopEnd
                                onClicked: backend.addSelectedLoopExitPoint(
                                    backend.playheadFrame
                                )
                            }
                            ListView {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 44
                                orientation: ListView.Horizontal
                                spacing: 6
                                clip: true
                                model: backend.selectedLoopExitPoints
                                delegate: TouchButton {
                                    required property int modelData
                                    text: backend.timeText(modelData) + "  ×"
                                    height: 42
                                    onClicked: backend.removeSelectedLoopExitPoint(modelData)
                                }
                                ScrollBar.horizontal: ScrollBar {
                                    policy: ScrollBar.AsNeeded
                                }
                            }
                        }
                        Label { text: root.selectedArea === "loop" && backend.selectedLoopId === "main" ? "The Main Loop starts automatically after the Intro." : ""; color: "#91a5b9" }
                    }
                }
            }

            // BEAT & METER
            Frame {
                RowLayout {
                    anchors.fill: parent; spacing: 18
                    ColumnLayout {
                        Layout.fillWidth: true
                        ValueEditor { title: "BPM"; text: backend.bpm.toFixed(2); onCommitted: value => backend.setBpm(Number(value)) }
                        Label { text: "Detected: " + backend.detectedBpm.toFixed(2) + " BPM"; color: "#899bad" }
                        RowLayout {
                            Repeater {
                                model: [{t:"−1",v:-1}, {t:"−0.5",v:-0.5}, {t:"−0.1",v:-0.1}, {t:"+0.1",v:0.1}, {t:"+0.5",v:0.5}, {t:"+1",v:1}]
                                TouchButton { required property var modelData; text: modelData.t; onClicked: backend.adjustBpm(modelData.v) }
                            }
                        }
                        TouchButton { text: "Automatic BPM"; onClicked: backend.resetBpm() }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        RowLayout {
                            Label { text: "Time Signature"; Layout.preferredWidth: 110 }
                            SpinBox { id: numerator; from: 1; to: 32; value: backend.beatsPerBar; onValueModified: backend.setTimeSignature(value, denominator.currentValue) }
                            Label { text: "/" }
                            ComboBox { id: denominator; model: [1,2,4,8,16]; currentIndex: Math.max(0, model.indexOf(backend.beatUnit)); onActivated: backend.setTimeSignature(numerator.value, currentValue) }
                        }
                        RowLayout {
                            Label { text: "Downbeat"; Layout.preferredWidth: 110 }
                            SpinBox { from: 0; to: Math.max(0, backend.beatsPerBar - 1); value: backend.downbeatOffset; onValueModified: backend.setDownbeatOffset(value) }
                        }
                        RowLayout {
                            Label { text: "Grid"; Layout.preferredWidth: 110 }
                            TouchButton { text: "−10 ms"; onClicked: backend.shiftBeatGrid(-480) }
                            TouchButton { text: "+10 ms"; onClicked: backend.shiftBeatGrid(480) }
                        }
                        Label { text: "Offset: " + backend.beatGridOffset + " Frames"; color: "#899bad" }
                    }
                }
            }

            // Track details have no waveform and can scroll on small displays.
            Frame {
                ScrollView {
                    id: trackDetailsScroll
                    anchors.fill: parent
                    clip: true

                    ColumnLayout {
                        x: Math.max(20, (trackDetailsScroll.availableWidth - width) / 2)
                        width: Math.max(0, Math.min(720, trackDetailsScroll.availableWidth - 40))
                        spacing: 10
                        Label { text: "Track Details"; font.pixelSize: 24; font.bold: true }
                        ValueEditor { objectName: "displayNameEditor"; title: "Display Name"; text: backend.displayName; liveEdit: true; onEdited: value => backend.setText("title", value) }
                        ValueEditor { title: "File Name"; text: backend.fileName; readOnly: true }
                        ValueEditor { objectName: "artistEditor"; title: "Artist"; text: backend.artist; liveEdit: true; onEdited: value => backend.setText("artist", value) }
                        ValueEditor { objectName: "albumEditor"; title: "Album"; text: backend.album; liveEdit: true; onEdited: value => backend.setText("album", value) }
                        ValueEditor { objectName: "categoryEditor"; title: "Category"; text: backend.category; liveEdit: true; onEdited: value => backend.setText("category", value) }
                        ValueEditor { objectName: "tagsEditor"; title: "Tags"; text: backend.tags; liveEdit: true; onEdited: value => backend.setTags(value) }
                        Label { text: "Attribution"; color: "#b9c7d6" }
                        ScrollView {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 130
                            TextArea {
                                id: attributionInput
                                objectName: "attributionInput"
                                text: backend.attribution
                                placeholderText: "Credits, license, source URL, usage terms…"
                                wrapMode: TextEdit.Wrap
                                selectByMouse: true
                                color: "#e7edf4"
                                font.pixelSize: 15
                                background: Rectangle {
                                    color: "#1a2632"
                                    border.color: "#3a4c5e"
                                    radius: 5
                                }
                                onTextChanged: {
                                    if (activeFocus && text !== backend.attribution)
                                        backend.setText("attribution", text)
                                }
                            }
                        }
                        Item { Layout.preferredHeight: 8 }
                        TouchButton {
                            text: "Archive Track"
                            baseColor: "#683c46"
                            foregroundColor: "#fff1f3"
                            Layout.alignment: Qt.AlignRight
                            Layout.preferredWidth: 170
                            onClicked: archiveConfirm.open()
                        }
                    }
                }
            }

            // LOUDNESS, intentionally without a waveform
            Frame {
                ColumnLayout {
                    anchors.centerIn: parent; width: Math.min(700, parent.width - 40); spacing: 12
                    Label { text: "Loudness"; font.pixelSize: 24; font.bold: true }
                    Label { text: "Integrated: " + backend.integratedLufs.toFixed(2) + " LUFS"; font.pixelSize: 17 }
                    Label { text: "Target: " + backend.targetLufs.toFixed(1) + " LUFS" }
                    Label { text: "Sample Peak: " + backend.samplePeak.toFixed(2) + " dBFS" }
                    Label { text: "Normalization: " + backend.normalizationGain.toFixed(2) + " dB" }
                    CheckBox { text: "Normalization Enabled"; checked: backend.normalizationEnabled; onToggled: backend.setNormalizationEnabled(checked) }
                    RowLayout {
                        TouchButton { text: "−0.5 dB"; onClicked: backend.setLoudnessOffset(backend.loudnessOffset - 0.5) }
                        Label { text: (backend.loudnessOffset >= 0 ? "+" : "") + backend.loudnessOffset.toFixed(1) + " dB"; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter }
                        TouchButton { text: "+0.5 dB"; onClicked: backend.setLoudnessOffset(backend.loudnessOffset + 0.5) }
                    }
                    Label { text: "Analyzed PS/PE: " + backend.timeText(backend.detectedPlayStart) + " – " + backend.timeText(backend.detectedPlayEnd) }
                    TouchButton { text: "Reanalyze Silence"; onClicked: backend.reanalyze() }
                }
            }
        }
    }

    Popup {
        id: archiveConfirm
        objectName: "archiveTrackConfirm"
        anchors.centerIn: parent
        width: Math.min(520, root.width - 40)
        height: 230
        padding: 0
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            radius: 10
            color: "#17222c"
            border.width: 1
            border.color: "#76535b"
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 12
            Label {
                text: "Archive Track?"
                font.pixelSize: 22
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: "#b9c7d6"
                text: backend.dirty
                      ? "Current changes will be saved first. The audio file will remain in data/music."
                      : "Metadata and waveform cache will be moved to the archive. The audio file will remain in data/music."
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                TouchButton {
                    text: "Cancel"
                    onClicked: archiveConfirm.close()
                }
                Item { Layout.fillWidth: true }
                TouchButton {
                    text: "Archive Track"
                    baseColor: "#683c46"
                    onClicked: {
                        if (backend.dirty && !backend.save())
                            return
                        if (backend.archiveCurrent()) {
                            archiveConfirm.close()
                            root.trackArchived()
                        }
                    }
                }
            }
        }
    }
}
