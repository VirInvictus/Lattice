from mutagen.oggopus import OggOpus
opus_audio = OggOpus()
opus_audio.add_tags()
print("metadata_block_picture" not in opus_audio)
opus_audio["metadata_block_picture"] = []
opus_audio["metadata_block_picture"].append("foo")
