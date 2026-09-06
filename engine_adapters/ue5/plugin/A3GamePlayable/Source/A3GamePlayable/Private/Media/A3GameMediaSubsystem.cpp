#include "Media/A3GameMediaSubsystem.h"

#include "MediaPlayer.h"
#include "MediaSoundComponent.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Dom/JsonObject.h"
#include "UObject/Package.h"

bool UA3GameMediaSubsystem::DoesSupportWorldType(const EWorldType::Type WorldType) const
{
    return WorldType == EWorldType::Game || WorldType == EWorldType::PIE;
}

void UA3GameMediaSubsystem::Deinitialize()
{
    for (TPair<FString, FMediaBinding>& Pair : Bindings)
    {
        if (Pair.Value.Player)
        {
            Pair.Value.Player->OnEndReached.RemoveDynamic(this, &UA3GameMediaSubsystem::HandleMediaEnd);
            Pair.Value.Player->Close();
            Pair.Value.Player->RemoveFromRoot();
        }
    }
    SetGameplayPaused(false);
    Bindings.Reset();
    EventLog.Reset();
    ActiveVideoKey.Reset();
    Super::Deinitialize();
}

bool UA3GameMediaSubsystem::RegisterMedia(const FString& EventKey, const FString& SourcePath, bool bVideo)
{
    if (EventKey.TrimStartAndEnd().IsEmpty() || SourcePath.TrimStartAndEnd().IsEmpty())
    {
        return false;
    }

    if (FMediaBinding* Existing = Bindings.Find(EventKey))
    {
        if (Existing->Player)
        {
            Existing->Player->OnEndReached.RemoveDynamic(this, &UA3GameMediaSubsystem::HandleMediaEnd);
            Existing->Player->Close();
            Existing->Player->RemoveFromRoot();
        }
        Bindings.Remove(EventKey);
    }

    UMediaPlayer* Player = NewObject<UMediaPlayer>(this);
    if (!Player)
    {
        return false;
    }
    Player->AddToRoot();
    Player->OnEndReached.AddDynamic(this, &UA3GameMediaSubsystem::HandleMediaEnd);
    UMediaSoundComponent* Sound = NewObject<UMediaSoundComponent>(this);
    if (Sound)
    {
        Sound->SetMediaPlayer(Player);
        Sound->RegisterComponentWithWorld(GetWorld());
    }

    FMediaBinding Binding;
    Binding.Player = Player;
    Binding.Sound = Sound;
    Binding.SourcePath = FPaths::FileExists(SourcePath)
        ? FPaths::ConvertRelativePathToFull(SourcePath)
        : SourcePath;
    const FString Extension = FPaths::GetExtension(SourcePath).ToLower();
    Binding.bVideo = bVideo || Extension == TEXT("mp4") || Extension == TEXT("mov") || Extension == TEXT("wmv") || Extension == TEXT("webm");
    Bindings.Add(EventKey, MoveTemp(Binding));
    RecordEvent(TEXT("media_registered"), EventKey, TEXT("setup"), true);
    return true;
}

bool UA3GameMediaSubsystem::RegisterAudio(const FString& EventKey, const FString& SourcePath)
{
    return RegisterMedia(EventKey, SourcePath, false);
}

bool UA3GameMediaSubsystem::RegisterCG(const FString& EventKey, const FString& VideoUrl)
{
    return RegisterMedia(EventKey, VideoUrl, true);
}

bool UA3GameMediaSubsystem::TriggerMedia(const FString& EventKey, const FString& TriggerSource)
{
    FMediaBinding* Binding = Bindings.Find(EventKey);
    FString MediaUrl;
    if (Binding)
    {
        MediaUrl = Binding->SourcePath;
        if (FPaths::FileExists(MediaUrl))
        {
            MediaUrl = FString::Printf(TEXT("file://%s"), *MediaUrl.Replace(TEXT("\\"), TEXT("/")));
        }
    }
    const bool bIssued = Binding && Binding->Player && Binding->Player->OpenUrl(MediaUrl);
    if (bIssued)
    {
        if (Binding->bVideo)
        {
            SetGameplayPaused(true);
            ActiveVideoKey = EventKey;
            Binding->Player->SetLooping(false);
        }
        Binding->Player->Play();
    }
    RecordEvent(Binding && Binding->bVideo ? TEXT("cg_triggered") : TEXT("audio_triggered"), EventKey, TriggerSource, bIssued);
    return bIssued;
}

bool UA3GameMediaSubsystem::TriggerAudio(const FString& EventKey, const FString& TriggerSource)
{
    const FMediaBinding* Binding = Bindings.Find(EventKey);
    if (!Binding || Binding->bVideo)
    {
        RecordEvent(TEXT("audio_triggered"), EventKey, TriggerSource, false);
        return false;
    }
    return TriggerMedia(EventKey, TriggerSource);
}

bool UA3GameMediaSubsystem::TriggerCG(const FString& EventKey, const FString& TriggerSource)
{
    const FMediaBinding* Binding = Bindings.Find(EventKey);
    if (!Binding || !Binding->bVideo)
    {
        RecordEvent(TEXT("cg_triggered"), EventKey, TriggerSource, false);
        return false;
    }
    return TriggerMedia(EventKey, TriggerSource);
}

bool UA3GameMediaSubsystem::StopMedia(const FString& EventKey)
{
    FMediaBinding* Binding = Bindings.Find(EventKey);
    if (!Binding || !Binding->Player)
    {
        return false;
    }
    Binding->Player->Pause();
    Binding->Player->Close();
    if (Binding->bVideo)
    {
        if (ActiveVideoKey == EventKey)
        {
            ActiveVideoKey.Reset();
        }
        SetGameplayPaused(false);
    }
    RecordEvent(TEXT("media_stopped"), EventKey, TEXT("gameplay"), true);
    return true;
}

bool UA3GameMediaSubsystem::StopCG(const FString& EventKey, const FString& TriggerSource)
{
    FMediaBinding* Binding = Bindings.Find(EventKey);
    if (!Binding || !Binding->bVideo || !Binding->Player)
    {
        return false;
    }
    Binding->Player->Pause();
    Binding->Player->Close();
    ActiveVideoKey.Reset();
    SetGameplayPaused(false);
    RecordEvent(TEXT("cg_stopped"), EventKey, TriggerSource, true);
    return true;
}

UMediaPlayer* UA3GameMediaSubsystem::GetMediaPlayer(const FString& EventKey) const
{
    const FMediaBinding* Binding = Bindings.Find(EventKey);
    return Binding ? Binding->Player.Get() : nullptr;
}

FString UA3GameMediaSubsystem::GetEventLogJson() const
{
    return FString::Join(EventLog, TEXT("\n"));
}

void UA3GameMediaSubsystem::RecordEvent(const FString& EventType, const FString& EventKey, const FString& TriggerSource, bool bIssued)
{
    TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
    Object->SetStringField(TEXT("schema_version"), TEXT("gamefactory3a.media_runtime_event.v1"));
    Object->SetNumberField(TEXT("seq"), EventLog.Num() + 1);
    Object->SetNumberField(TEXT("t_monotonic_ms"), FPlatformTime::Seconds() * 1000.0);
    Object->SetStringField(TEXT("event_type"), EventType);
    Object->SetStringField(TEXT("event_key"), EventKey);
    Object->SetStringField(TEXT("trigger_source"), TriggerSource);
    Object->SetBoolField(TEXT("playback_call_issued"), bIssued);
    Object->SetStringField(TEXT("asset_path"), Bindings.Contains(EventKey) ? Bindings[EventKey].SourcePath : TEXT(""));
    FString Json;
    TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Json);
    FJsonSerializer::Serialize(Object, Writer);
    EventLog.Add(Json);
    OnMediaEvent.Broadcast(Json);
}

void UA3GameMediaSubsystem::SetGameplayPaused(bool bPaused)
{
    if (bGameplayPaused == bPaused)
    {
        return;
    }
    bGameplayPaused = bPaused;
    OnGameplayPauseChanged.Broadcast(bGameplayPaused);
}

void UA3GameMediaSubsystem::HandleMediaEnd()
{
    if (ActiveVideoKey.IsEmpty())
    {
        return;
    }
    const FString FinishedKey = ActiveVideoKey;
    ActiveVideoKey.Reset();
    SetGameplayPaused(false);
    RecordEvent(TEXT("cg_finished"), FinishedKey, TEXT("engine"), true);
}





