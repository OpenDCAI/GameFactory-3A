#pragma once

#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"
#include "A3GamePlaytestRecorderSubsystem.generated.h"

UCLASS()
class A3GAMEPLAYABLE_API UA3GamePlaytestRecorderSubsystem
    : public UTickableWorldSubsystem
{
    GENERATED_BODY()

public:
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;
    virtual bool DoesSupportWorldType(const EWorldType::Type WorldType) const override;
    virtual void Tick(float DeltaTime) override;
    virtual TStatId GetStatId() const override;
    virtual bool IsTickable() const override;

private:
    void StartFromCommandLine();
    void StopRecording();
    void WriteMarkerFile() const;
    void WriteReportFile() const;

    FString OutputDir;
    FString FramesDir;
    double StartedAt = 0.0;
    double NextCaptureAt = 0.0;
    double DurationSeconds = 0.0;
    double CaptureInterval = 0.05;
    int32 FrameIndex = 0;
    bool bArmed = false;
    bool bRecording = false;
};
