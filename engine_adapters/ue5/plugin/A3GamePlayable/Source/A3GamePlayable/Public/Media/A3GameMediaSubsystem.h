#pragma once

#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"
#include "A3GameMediaSubsystem.generated.h"

class UMediaPlayer;
class UMediaSoundComponent;

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FA3GameMediaEvent, const FString&, EventJson);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FA3GameGameplayPauseChanged, bool, bPaused);

UCLASS(BlueprintType)
class A3GAMEPLAYABLE_API UA3GameMediaSubsystem : public UWorldSubsystem
{
    GENERATED_BODY()

public:
    virtual bool DoesSupportWorldType(const EWorldType::Type WorldType) const override;
    virtual void Deinitialize() override;

    UFUNCTION(BlueprintCallable, Category = "A3Game|Media")
    bool RegisterMedia(const FString& EventKey, const FString& SourcePath, bool bVideo);

    UFUNCTION(BlueprintCallable, Category = "A3Game|Media")
    bool RegisterAudio(const FString& EventKey, const FString& SourcePath);

    UFUNCTION(BlueprintCallable, Category = "A3Game|Media")
    bool RegisterCG(const FString& EventKey, const FString& VideoUrl);

    UFUNCTION(BlueprintCallable, Category = "A3Game|Media")
    bool TriggerMedia(const FString& EventKey, const FString& TriggerSource = TEXT("gameplay"));

    UFUNCTION(BlueprintCallable, Category = "A3Game|Media")
    bool TriggerAudio(const FString& EventKey, const FString& TriggerSource = TEXT("gameplay"));

    UFUNCTION(BlueprintCallable, Category = "A3Game|Media")
    bool TriggerCG(const FString& EventKey, const FString& TriggerSource = TEXT("gameplay"));

    UFUNCTION(BlueprintCallable, Category = "A3Game|Media")
    bool StopMedia(const FString& EventKey);

    UFUNCTION(BlueprintCallable, Category = "A3Game|Media")
    bool StopCG(const FString& EventKey, const FString& TriggerSource = TEXT("gameplay"));

    UFUNCTION(BlueprintPure, Category = "A3Game|Media")
    UMediaPlayer* GetMediaPlayer(const FString& EventKey) const;

    UFUNCTION(BlueprintPure, Category = "A3Game|Media")
    FString GetEventLogJson() const;

    UFUNCTION(BlueprintPure, Category = "A3Game|Media")
    bool IsGameplayPaused() const { return bGameplayPaused; }

    UPROPERTY(BlueprintAssignable, Category = "A3Game|Media")
    FA3GameMediaEvent OnMediaEvent;

    UPROPERTY(BlueprintAssignable, Category = "A3Game|Media")
    FA3GameGameplayPauseChanged OnGameplayPauseChanged;

private:
    struct FMediaBinding
    {
        TObjectPtr<UMediaPlayer> Player;
        TObjectPtr<UMediaSoundComponent> Sound;
        FString SourcePath;
        bool bVideo = false;
    };

    void RecordEvent(const FString& EventType, const FString& EventKey, const FString& TriggerSource, bool bIssued);
    void SetGameplayPaused(bool bPaused);

    UFUNCTION()
    void HandleMediaEnd();

    TMap<FString, FMediaBinding> Bindings;
    TArray<FString> EventLog;
    FString ActiveVideoKey;
    bool bGameplayPaused = false;
};
