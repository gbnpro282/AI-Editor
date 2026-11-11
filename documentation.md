# AI Local Video Editor Studio Pro

## English

### Overview

AI Local Video Editor Studio Pro is a desktop application for assembling source video clips into a video. A soundtrack and reference video are optional: without a soundtrack it uses source-video audio, and without a reference it uses learned preferences and any supplied prompt. It can rank source clips with a local Ollama vision model and renders the result with FFmpeg.

### Features

- Select a folder of source videos and an output folder; soundtrack and reference video are optional.
- Choose clip timing based on the reference video, music beats, or a hybrid of both.
- Optionally rank clips by scoring a frame from each video with an Ollama vision model.
- Sample video segments across the full duration of source videos, optionally rank sampled scenes with Ollama, and use their source timestamps instead of always taking the beginning.
- Speed up sample evaluation by reusing in-memory JPEG frames, a persistent local Ollama connection, cached preference data, and keeping the vision model loaded during scoring; show rolling remaining-time and estimated-finish updates without changing frame coverage or image quality settings.
- Review the rendered video in-app, with playback pausing at every scene boundary for a 1–5 star rating and optional feedback.
- Create a Windows-compatible WMV preview with the rendered audio, plus Skip-to-completion and Replay controls.
- Store compact scene ratings and perceptual hashes in `Memory Learning/memory_learning.db`; use prior ratings to personalize later clip scoring.
- Run decode, stream, duration, black/freeze, and frame-timing QC on the render and playback preview before either is shown or exported. When QC finds a problem, replace affected scenes and retry rendering up to two times; if QC still fails, choose **Cancel**, **generate ulang semua video**, or **generate scene**. Every new render must pass QC before preview/export is enabled.
- Summarize previous ratings and repeated positive/negative comment terms into local preference context for later Ollama scoring.
- Enter an optional per-edit **External Promt** in the setup panel; Ollama combines it with saved ratings, comments, and previous prompts when selecting scenes.
- Save a reviewed render to a chosen location or regenerate the edit using saved ratings and an optional global prompt.
- Scan all source videos or limit the AI scan to the first 30 videos.
- Recursively filter excluded folders and file patterns before inspection; stream paths with `os.scandir`, inspect container headers and file tails in 64 KiB chunks, and decode sampled frames in a two-worker pool. Cache successful inspection results in `.file_scan_cache.db` by path, size, and modification time.
- Choose a 9:16, 16:9, 1:1, or 4:5 output size.
- Set a 15-, 30-, or 60-second duration, or use the full soundtrack duration.
- Keep or mute source-video audio, mix it with an optional soundtrack, and optionally apply FFmpeg's `deshake` filter.
- Render H.264 video using NVIDIA NVENC at 60 FPS, with AAC audio from the soundtrack or source clips.

### Requirements

- Windows with Python and the required Python packages installed.
- FFmpeg available on `PATH`, built with the `h264_nvenc` encoder.
- An NVIDIA GPU and compatible driver for NVENC rendering.
- Ollama running locally with a compatible vision model installed to use AI clip ranking. The default model is `qwen2.5vl:3b`. Ollama-based ranking can be disabled in the application.

The application imports PyQt5, requests, NumPy, OpenCV, librosa, and PySceneDetect.

### Setup

1. Install Python packages:

   ```powershell
   python -m pip install PyQt5 requests numpy opencv-python librosa scenedetect
   ```

2. Install FFmpeg, ensure its `bin` directory is on `PATH`, and verify that the build supports `h264_nvenc`.
3. To enable AI clip ranking, install and start Ollama, then download the default model:

   ```powershell
   ollama pull qwen2.5vl:3b
   ```

   The application communicates with Ollama at `http://localhost:11434`.

### Launch

From the folder containing the application:

```powershell
python AI_Video_Studio_Pro.py
```

### Using the application

1. Choose the folder containing source videos. Supported extensions are `.mp4`, `.mov`, `.avi`, and `.mkv`.
2. Optionally choose a soundtrack (`.mp3`, `.wav`, `.aac`, or `.m4a`) and a reference video (`.mp4`, `.mov`, `.mkv`, or `.avi`). If no soundtrack is selected, source-video audio is used; if no reference is selected, default timing and learned preferences are used.
3. Choose an output folder.
4. Select a timing mode:
   - **Reference**: follows scene-duration patterns detected in the reference video, or default timing when no reference is selected.
   - **Beat**: uses detected gaps between music beats, or default timing when no soundtrack is selected.
   - **Hybrid**: combines reference durations with nearby beat timings when available; missing inputs use default timing.
5. Set the duration preset, output aspect ratio, and optional audio-muting and stabilization settings.
6. Enable or disable Ollama ranking. When enabled, choose an installed model and whether to scan all videos. The limited scan checks up to the first 30 videos.
7. Optionally enter an **External Promt** in the setup panel to describe this edit. The prompt is combined with learned ratings, comments, and previous edit prompts. Entering a prompt automatically uses the selected Ollama model for scene scoring, even if the ranking checkbox is off.
8. Start processing and follow progress in the application log. Before showing the preview, QC checks decoding, audio/video streams, duration, near-black/frozen sections, and irregular frame timestamps on both the MP4 render and WMV preview. If a problem is detected, the app replaces affected scenes and retries up to two times. If QC still fails, choose **Cancel**, **generate ulang semua video** (replace scenes across the full video), or **generate scene** (replace only affected scenes). Every retry is checked again; preview and export remain unavailable until QC passes. Reports are stored in `Memory Learning/memory_learning.db`. These automated checks flag likely defects but cannot guarantee detection of every visual issue; review the passing preview before saving.
9. Review the embedded Windows-compatible preview (a `_preview.wmv` sidecar with the rendered audio). Playback starts automatically and pauses at each scene end; choose 1–5 stars, optionally add a comment, and confirm to continue. Use **Skip** to go straight to the completion panel or **Replay** to restart the scene review. Ratings and the current external prompt are stored locally in `Memory Learning/memory_learning.db`.
10. After the final scene, use **Save Video** to copy the original MP4 render to a chosen file path, or **Regenerate Edit** to run the pipeline again with learned ratings and an optional global prompt. Ollama scoring uses up to five prior positive/negative examples, previous external prompts and a summary of comment preferences; visually similar clips also receive a preference-based score adjustment.

### Notes

- Candidate segments are sampled at randomized positions distributed from the beginning through the end of each source video. With Ollama ranking enabled, the best-rated samples are selected and kept in chronological order within each source file; without it, samples are chosen across the timeline without AI ranking.
- Source discovery streams nested folders with `os.scandir` and honors the selected folder's `.gitignore`; `.git`, `.venv`, `.idea`, `node_modules`, `__pycache__`, `Output Video`, `Memory Learning`, `*.tmp`, `*.preview.*`, `*.log`, and `*.bak` are excluded. Uncached files have their container header and tail fingerprinted in chunks of at most 64 KiB and sampled frames decoded; successful inspection metadata is reused from `.file_scan_cache.db` when path, size, and modification time match. The full temporal scene-sampling pass remains enabled.
- When a soundtrack is selected, it is the primary audio; source-video audio is mixed in when mute is off. Without a soundtrack, source-video audio is used unless muted. If no usable audio is available, a silent track is rendered.
- Rendering can take substantial time and disk space, especially at 4K. Actual performance depends on the hardware, media, and FFmpeg build.
- If Ollama is unavailable or a frame cannot be scored, the application may use its built-in default score. Check the application log and terminal output when troubleshooting.
- Ratings, comments, external prompts, preference summaries, and render quality reports are stored locally in `Memory Learning/memory_learning.db`. Keep this database to preserve learned preferences and past check reports between launches.
- The `_preview.wmv` file is a playback proxy; the original H.264 MP4 remains the file saved/exported by the completion panel.

### Troubleshooting

- **FFmpeg not found**: add FFmpeg's `bin` directory to `PATH`, then restart the application.
- **NVENC encoder error**: verify NVIDIA drivers, GPU support, and that FFmpeg includes `h264_nvenc`.
- **Ollama model missing or unavailable**: start Ollama and install a vision model; alternatively disable AI clip ranking.
- **No source clips detected**: check that the selected folder contains videos with one of the supported extensions.
- **Invalid input warning**: confirm that all selected paths exist and point to the expected file or folder type.

---

## Bahasa Indonesia

### Ringkasan

AI Local Video Editor Studio Pro adalah aplikasi desktop untuk menyusun klip video bahan menjadi sebuah video. Soundtrack dan video referensi bersifat opsional: tanpa soundtrack, audio diambil dari video bahan; tanpa referensi, aplikasi menggunakan preferensi yang dipelajari dan prompt jika diisi. Aplikasi dapat memberi peringkat klip dengan model vision Ollama lokal, lalu merender hasil menggunakan FFmpeg.

### Fitur

- Memilih folder video bahan dan folder hasil; soundtrack dan video referensi bersifat opsional.
- Menentukan timing potongan berdasarkan video referensi, beat musik, atau gabungan keduanya.
- Memberi peringkat klip secara opsional dengan menilai satu frame dari setiap video menggunakan model vision Ollama.
- Mengambil sampel klip secara acak-tersebar dari seluruh durasi video bahan, memberi peringkat adegan secara opsional dengan Ollama, dan menggunakan timestamp sumbernya—bukan selalu mengambil bagian awal.
- Meninjau hasil render di dalam aplikasi; playback berhenti pada batas setiap adegan untuk rating 1–5 bintang dan komentar opsional.
- Membuat preview WMV yang kompatibel dengan Windows dan berisi audio hasil render, serta tombol Lewati ke panel akhir dan Putar Ulang.
- Menyimpan rating dan perceptual hash ringkas ke `Memory Learning/memory_learning.db`; rating sebelumnya digunakan untuk mempersonalisasi skor klip berikutnya.
- Menjalankan QC decode, stream, durasi, frame hitam/beku, dan timing pada hasil render serta preview sebelum ditampilkan atau diekspor. Jika ada masalah, adegan terkait diganti dan render dicoba ulang maksimal dua kali. Jika QC tetap gagal, pengguna dapat memilih **Cancel**, **generate ulang semua video**, atau **generate scene**; preview/ekspor hanya dibuka setelah render berikutnya lulus QC.
- Merangkum rating lama dan istilah komentar positif/negatif berulang sebagai konteks preferensi untuk penilaian Ollama berikutnya.
- Memasukkan **External Promt** opsional untuk tiap edit dari panel pengaturan; Ollama menggabungkannya dengan rating, komentar, dan prompt edit sebelumnya saat memilih adegan.
- Menyimpan hasil render ke lokasi pilihan atau membuat ulang edit dengan rating tersimpan dan prompt global opsional.
- Memindai semua video bahan atau membatasi pemindaian AI pada 30 video pertama.
- Memfilter folder dan pola berkas yang dikecualikan sebelum inspeksi; menelusuri jalur dengan `os.scandir`, memeriksa header dan bagian akhir kontainer dalam blok 64 KiB, serta mendekode frame sampel melalui worker pool dua pekerja. Hasil inspeksi berhasil disimpan di `.file_scan_cache.db` berdasarkan jalur, ukuran, dan waktu modifikasi.
- Mempercepat evaluasi sampel dengan memakai ulang frame JPEG di memori, koneksi Ollama lokal, data preferensi yang di-cache, dan menjaga model tetap dimuat selama penilaian; menampilkan pembaruan sisa waktu dan perkiraan selesai tanpa mengubah cakupan sampel atau pengaturan kualitas gambar.
- Memilih ukuran output 9:16, 16:9, 1:1, atau 4:5.
- Mengatur durasi 15, 30, atau 60 detik, atau mengikuti durasi penuh soundtrack.
- Mempertahankan atau mematikan audio video bahan, mencampurnya dengan soundtrack opsional, serta mengaktifkan filter `deshake` FFmpeg secara opsional.
- Merender video H.264 menggunakan NVIDIA NVENC pada 60 FPS, dengan audio AAC dari soundtrack atau video bahan.

### Persyaratan

- Windows dengan Python dan paket Python yang diperlukan.
- FFmpeg tersedia melalui `PATH` dan dibangun dengan encoder `h264_nvenc`.
- GPU NVIDIA dan driver yang kompatibel untuk rendering NVENC.
- Ollama yang berjalan secara lokal dan model vision yang sudah terpasang untuk menggunakan pemeringkatan klip AI. Model bawaan adalah `qwen2.5vl:3b`. Pemeringkatan melalui Ollama dapat dinonaktifkan di aplikasi.

Aplikasi mengimpor PyQt5, requests, NumPy, OpenCV, librosa, dan PySceneDetect.

### Persiapan

1. Pasang paket Python:

   ```powershell
   python -m pip install PyQt5 requests numpy opencv-python librosa scenedetect
   ```

2. Pasang FFmpeg, tambahkan direktori `bin` ke `PATH`, dan pastikan build FFmpeg mendukung `h264_nvenc`.
3. Untuk menggunakan pemeringkatan klip AI, pasang dan jalankan Ollama, lalu unduh model bawaan:

   ```powershell
   ollama pull qwen2.5vl:3b
   ```

   Aplikasi terhubung ke Ollama melalui `http://localhost:11434`.

### Menjalankan aplikasi

Buka PowerShell pada folder aplikasi, lalu jalankan:

```powershell
python AI_Video_Studio_Pro.py
```

### Cara menggunakan

1. Pilih folder video bahan. Ekstensi yang didukung: `.mp4`, `.mov`, `.avi`, dan `.mkv`.
2. Soundtrack (`.mp3`, `.wav`, `.aac`, atau `.m4a`) dan video referensi (`.mp4`, `.mov`, `.mkv`, atau `.avi`) boleh dikosongkan. Tanpa soundtrack, audio video bahan digunakan; tanpa referensi, timing default dan preferensi yang dipelajari digunakan.
3. Pilih folder output.
4. Pilih mode timing:
   - **Reference**: mengikuti pola durasi adegan dari referensi, atau timing default jika referensi tidak dipilih.
   - **Beat**: menggunakan jarak antar-beat musik, atau timing default jika soundtrack tidak dipilih.
   - **Hybrid**: menggabungkan durasi referensi dengan timing beat jika tersedia; input yang tidak dipilih menggunakan timing default.
5. Atur preset durasi, rasio output, serta opsi mute audio dan stabilisasi.
6. Aktifkan atau nonaktifkan pemeringkatan Ollama. Jika aktif, pilih model yang sudah terpasang dan tentukan apakah semua video akan dipindai. Pemindaian terbatas memeriksa maksimal 30 video pertama.
7. Opsional, isi **External Promt** pada panel pengaturan untuk menjelaskan edit ini. Prompt digabungkan dengan rating, komentar, dan prompt sebelumnya yang dipelajari. Jika prompt diisi, model Ollama yang dipilih akan digunakan untuk menilai adegan meskipun checkbox pemeringkatan tidak aktif.
8. Mulai proses dan pantau progres melalui log aplikasi. Sebelum preview ditampilkan, QC memeriksa decode, stream audio/video, durasi, bagian near-black/beku, dan timestamp frame pada MP4 serta preview WMV. Jika ada masalah, aplikasi mengganti adegan terkait dan mencoba render ulang maksimal dua kali. Jika QC tetap gagal, pilih **Cancel**, **generate ulang semua video** (mengganti adegan di seluruh video), atau **generate scene** (hanya mengganti adegan bermasalah). Setiap render ulang diperiksa lagi; preview dan ekspor hanya tersedia setelah QC lulus. Laporan disimpan ke `Memory Learning/memory_learning.db`. Pemeriksaan otomatis menandai kemungkinan cacat tetapi tidak menjamin semua masalah visual terdeteksi; tinjau preview yang lulus sebelum menyimpan.
9. Tinjau preview Windows-compatible (file pendamping `_preview.wmv` dengan audio hasil render). Playback berjalan otomatis lalu berhenti pada akhir setiap adegan; pilih rating 1–5 bintang, tambahkan komentar jika perlu, lalu konfirmasi untuk melanjutkan. Gunakan **Skip** untuk langsung ke panel akhir atau **Replay** untuk mengulang review dari awal. Rating dan external prompt saat ini disimpan secara lokal ke `Memory Learning/memory_learning.db`.
10. Setelah adegan terakhir, gunakan **Save Video** untuk menyalin hasil render MP4 asli ke lokasi pilihan, atau **Regenerate Edit** untuk menjalankan pipeline kembali dengan rating yang dipelajari dan prompt global opsional. Penilaian Ollama memakai hingga lima contoh rating positif/negatif, prompt eksternal sebelumnya, serta ringkasan preferensi dari komentar; klip yang mirip secara visual juga mendapat penyesuaian skor.

### Catatan

- Kandidat klip diambil dari posisi acak-tersebar mulai awal sampai akhir setiap video bahan. Jika pemeringkatan Ollama aktif, sampel dengan skor terbaik dipilih dan dijaga urutan waktunya dalam setiap file sumber; jika tidak, sampel dipilih dari berbagai waktu tanpa pemeringkatan AI.
- Pencarian video mencakup subfolder secara streaming dan mengikuti `.gitignore` pada folder yang dipilih; `.git`, `.venv`, `.idea`, `node_modules`, `__pycache__`, `Output Video`, `Memory Learning`, `*.tmp`, `*.preview.*`, `*.log`, dan `*.bak` dilewati. Berkas yang belum di-cache diperiksa pada header, bagian akhir, dan frame sampel dengan pembacaan maksimal 64 KiB per blok; hasil inspeksi berhasil yang ukurannya dan waktu modifikasinya tidak berubah digunakan kembali dari `.file_scan_cache.db`. Pemindaian sampel adegan seluruh rentang waktu tetap berjalan.
- Jika soundtrack dipilih, soundtrack menjadi audio utama; audio video bahan ikut dicampur saat opsi mute tidak aktif. Tanpa soundtrack, audio video bahan digunakan kecuali dimute. Jika audio tidak tersedia, render memakai track hening.
- Rendering dapat membutuhkan waktu dan ruang disk yang cukup besar, terutama pada resolusi 4K. Performa aktual bergantung pada perangkat keras, media, dan build FFmpeg.
- Jika Ollama tidak tersedia atau frame tidak dapat dinilai, aplikasi mungkin menggunakan skor bawaan. Periksa log aplikasi dan keluaran terminal saat melakukan pemecahan masalah.
- Rating, komentar, prompt eksternal, ringkasan preferensi, dan laporan kualitas render disimpan lokal di `Memory Learning/memory_learning.db`. Pertahankan database ini agar preferensi dan laporan pemeriksaan sebelumnya tetap tersedia setelah aplikasi ditutup.
- File `_preview.wmv` hanya proxy untuk playback; file MP4 H.264 asli tetap menjadi file yang disimpan/diekspor dari panel akhir.

### Pemecahan masalah

- **FFmpeg tidak ditemukan**: tambahkan direktori `bin` FFmpeg ke `PATH`, lalu mulai ulang aplikasi.
- **Kesalahan encoder NVENC**: periksa driver NVIDIA, dukungan GPU, dan pastikan FFmpeg menyertakan `h264_nvenc`.
- **Model Ollama tidak tersedia**: jalankan Ollama dan pasang model vision; atau nonaktifkan pemeringkatan klip AI.
- **Video bahan tidak terdeteksi**: pastikan folder yang dipilih berisi video dengan ekstensi yang didukung.
- **Peringatan input tidak valid**: pastikan semua path yang dipilih ada dan menunjuk ke jenis file atau folder yang benar.
