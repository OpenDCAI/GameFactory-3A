#include "Playtest/A3GamePlaytestRecorderSubsystem.h"

#include "HAL/PlatformFileManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformProcess.h"
#include "Misc/CommandLine.h"
#include "Misc/DefaultValueHelper.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "UnrealClient.h"
#include "Engine/World.h"

void UA3GamePlaytestRecorderSubsystem::Initialize(FSubsystemCollectionBase& Collection)
{
    Super::Initialize(Collection);
    UE_LOG(LogTemp, Log, TEXT("A3 playtest recorder initialized"));
    StartFromCommandLine();
}

bool UA3GamePlaytestRecorderSubsystem::DoesSupportWorldType(const EWorldType::Type WorldType) const
{
    return WorldType == EWorldType::Game || WorldType == EWorldType::PIE;
}

void UA3GamePlaytestRecorderSubsystem::Deinitialize()
{
    StopRecording();
    Super::Deinitialize();
}

void UA3GamePlaytestRecorderSubsystem::StartFromCommandLine()
{
    FString RequestedOutput;
    FString FpsValue;
    FString DurationValue;
    if (!FParse::Value(FCommandLine::Get(), TEXT("A3PlaytestOutput="), RequestedOutput))
    {
        UE_LOG(LogTemp, Log, TEXT("A3 playtest recorder inactive: no output argument"));
        return;
    }
    FParse::Value(FCommandLine::Get(), TEXT("A3PlaytestFps="), FpsValue);
    FParse::Value(FCommandLine::Get(), TEXT("A3PlaytestDuration="), DurationValue);
    int32 Fps = 20;
    double Duration = 0.0;
    FDefaultValueHelper::ParseInt(FpsValue, Fps);
    FDefaultValueHelper::ParseDouble(DurationValue, Duration);
    if (Fps <= 0 || Duration <= 0.0)
    {
        return;
    }
    OutputDir = FPaths::ConvertRelativePathToFull(RequestedOutput);
    FramesDir = FPaths::Combine(OutputDir, TEXT("frames"));
    IFileManager::Get().MakeDirectory(*FramesDir, true);
    DurationSeconds = Duration;
    CaptureInterval = 1.0 / static_cast<double>(Fps);
    FrameIndex = 0;
    // The take clock starts when gameplay actually begins, not when the
    // world is created; map loading otherwise eats the whole window.
    bArmed = true;
    UE_LOG(LogTemp, Log, TEXT("A3 playtest recorder armed: %s (%d fps, %.2f sec after gameplay starts)"), *FramesDir, Fps, Duration);
}

void UA3GamePlaytestRecorderSubsystem::StopRecording()
{
    bArmed = false;
    bRecording = false;
}

void UA3GamePlaytestRecorderSubsystem::WriteMarkerFile() const
{
    const FString Path = FPaths::Combine(OutputDir, TEXT("play_started.json"));
    const FString Json = FString::Printf(
        TEXT("{\n  \"native_recorder\": true,\n  \"pid\": %d\n}\n"),
        FPlatformProcess::GetCurrentProcessId());
    FFileHelper::SaveStringToFile(Json, *Path);
}

void UA3GamePlaytestRecorderSubsystem::WriteReportFile() const
{
    const bool bOk = FrameIndex > 0;
    const FString Path = FPaths::Combine(OutputDir, TEXT("_editor_report.json"));
    const FString Json = FString::Printf(
        TEXT("{\n  \"schema_version\": \"gamefactory3a.ue5.playtest_native_report.v1\",\n")
        TEXT("  \"source\": \"native_recorder\",\n  \"ok\": %s,\n  \"frames\": %d,\n  \"errors\": [%s]\n}\n"),
        bOk ? TEXT("true") : TEXT("false"),
        FrameIndex,
        bOk ? TEXT("") : TEXT("\"no frames were captured\""));
    FFileHelper::SaveStringToFile(Json, *Path);
}

void UA3GamePlaytestRecorderSubsystem::Tick(float DeltaTime)
{
    if (!bArmed && !bRecording)
    {
        return;
    }
    if (!bRecording)
    {
        UWorld* World = GetWorld();
        if (World == nullptr || !World->HasBegunPlay())
        {
            // Still booting or loading the map; the take has not begun.
            return;
        }
        // Gameplay is live: start the take clock and tell the host it may
        // send player input from this moment on.
        StartedAt = FPlatformTime::Seconds();
        NextCaptureAt = StartedAt;
        bRecording = true;
        WriteMarkerFile();
        UE_LOG(LogTemp, Log, TEXT("A3 playtest recorder started: %s (%.1f fps, %.2f sec)"), *FramesDir, 1.0 / CaptureInterval, DurationSeconds);
    }
    const double Now = FPlatformTime::Seconds();
    if (Now - StartedAt >= DurationSeconds)
    {
        StopRecording();
        WriteReportFile();
        // Finish the take like the Godot/Unity recorders do, so the host
        // does not sit through its kill timeout.
        FPlatformMisc::RequestExit(false);
        return;
    }
    if (Now < NextCaptureAt || FScreenshotRequest::IsScreenshotRequested())
    {
        return;
    }
    // Capture through the engine's screenshot pipeline (same as HighResShot):
    // it encodes a real PNG from the game viewport. Reading the viewport
    // with ReadPixels during the world tick hits an invalid render target
    // in -game mode and yields black frames. bShowUI must stay false: with
    // it true the editor path grabs the OS screen, which records whatever
    // window is in the foreground instead of the game.
    const FString Filename = FPaths::Combine(
        FramesDir,
        FString::Printf(TEXT("f%05d.png"), ++FrameIndex));
    FScreenshotRequest::RequestScreenshot(Filename, false, false);
    NextCaptureAt += CaptureInterval;
}

TStatId UA3GamePlaytestRecorderSubsystem::GetStatId() const
{
    RETURN_QUICK_DECLARE_CYCLE_STAT(UA3GamePlaytestRecorderSubsystem, STATGROUP_Tickables);
}

bool UA3GamePlaytestRecorderSubsystem::IsTickable() const
{
    return bArmed || bRecording;
}
