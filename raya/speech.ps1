# Windows-only offline speech adapter. JSON remains data on stdin.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
if ($request.operation -eq 'conversation') {
    Add-Type -AssemblyName System.Speech
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
    $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $events = New-Object System.Collections.ArrayList
    try {
        if ($request.voice) { $speaker.SelectVoice($request.voice) }
        $choices = New-Object System.Speech.Recognition.Choices
        $choices.Add([string[]]$request.phrases)
        $builder = New-Object System.Speech.Recognition.GrammarBuilder
        $builder.Culture = $engine.RecognizerInfo.Culture
        $builder.Append($choices)
        $engine.LoadGrammar([System.Speech.Recognition.Grammar]::new($builder))
        $engine.SetInputToDefaultAudioDevice()
        $null = Register-ObjectEvent $engine SpeechDetected -SourceIdentifier rayaDetected
        $null = Register-ObjectEvent $engine SpeechRecognized -SourceIdentifier rayaHeard
        $null = Register-ObjectEvent $engine RecognizeCompleted -SourceIdentifier rayaEnded
        $engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
        $timer = [Diagnostics.Stopwatch]::StartNew()
        while ($timer.Elapsed.TotalSeconds -lt $request.seconds) {
            $event = Wait-Event -Timeout 1
            if (!$event) { continue }
            Remove-Event -EventIdentifier $event.EventIdentifier
            if ($event.SourceIdentifier -eq 'rayaEnded') { throw 'Speech session disconnected' }
            if ($event.SourceIdentifier -eq 'rayaDetected') {
                $speaker.SpeakAsyncCancelAll()
            } elseif ($event.SourceIdentifier -eq 'rayaHeard') {
                $heard = $event.SourceEventArgs.Result.Text
                $null = $events.Add($heard)
                if ($heard -ceq $request.stop) { break }
                $answer = $request.answers.PSObject.Properties[$heard].Value
                if ($answer) { $null = $speaker.SpeakAsync([string]$answer) }
            }
        }
        @{ transcript = @($events); ended = $true } | ConvertTo-Json -Compress
    } finally {
        $speaker.SpeakAsyncCancelAll()
        $engine.RecognizeAsyncCancel()
        $engine.Dispose()
        $speaker.Dispose()
    }
    exit 0
}
$voice = New-Object -ComObject SAPI.SpVoice
$voices = @($voice.GetVoices() | ForEach-Object { $_.GetDescription() })
if ($request.operation -eq 'speak') {
    $tokens = $voice.GetVoices()
    if ($request.voice) {
        $found = $false
        foreach ($token in $tokens) {
            if ($token.GetDescription() -ceq $request.voice) {
                $voice.Voice = $token
                $found = $true
                break
            }
        }
        if (!$found) { throw 'Unknown voice' }
    }
    $null = $voice.Speak([string]$request.text, 16)
    @{ spoken = $true } | ConvertTo-Json -Compress
} else {
    $recognizer = New-Object -ComObject SAPI.SpInprocRecognizer
    $inputs = $recognizer.GetAudioInputs()
    if ($request.operation -eq 'devices') {
        @{ voices = $voices; inputs = @($inputs | ForEach-Object { $_.GetDescription() }) } | ConvertTo-Json -Compress
    } elseif ($request.operation -eq 'listen') {
        $target = @($inputs | Where-Object { $_.GetDescription() -ceq $request.device })
        if ($target.Count -ne 1) { throw 'Unknown or ambiguous microphone' }
        $recognizer.AudioInput = $target[0]
        $context = $recognizer.CreateRecoContext()
        $grammar = $context.CreateGrammar(1)
        $grammar.DictationSetState(1)
        $null = Register-ObjectEvent $context Recognition -SourceIdentifier rayaRecognition
        try {
            $event = Wait-Event -SourceIdentifier rayaRecognition -Timeout 12
            if (!$event) { throw 'No recognition within 12 seconds' }
            @{ text = $event.SourceEventArgs.Result.PhraseInfo.GetText() } | ConvertTo-Json -Compress
        } finally {
            $grammar.DictationSetState(0)
            Unregister-Event -SourceIdentifier rayaRecognition
        }
    } else { throw 'Unknown speech operation' }
}
