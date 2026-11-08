# AI Local Video Editor Studio Pro

## English

### Overview

AI Local Video Editor Studio Pro is a desktop application for assembling source video clips into a music-synchronized video. It analyzes the soundtrack for beats, analyzes a reference video's scene timing, can rank source clips with a local Ollama vision model, and renders the result with FFmpeg.

### Features

- Select a folder of source videos, a soundtrack, a reference video, and an output folder.
- Choose clip timing based on the reference video, music beats, or a hybrid of both.
- Optionally rank clips by scoring a frame from each video with an Ollama vision model.
- Sample video segments across the full duration of source videos, optionally rank sampled scenes with Ollama, and use their source timestamps instead of always taking the beginning.
- Review the rendered video in-app, with playback pausing at every scene boundary for a 1–5 star rating and optional feedback.
- Create a Windows-compatible WMV preview with soundtrack audio, plus Skip-to-completion and Replay controls.
- Store compact scene ratings and perceptual hashes in `Memory Learning/memory_learning.db`; use prior ratings to personalize later clip scoring.
- Run an automatic FFmpeg decode/quality check after rendering; flag long black/frozen intervals or missing streams, and store each check report in the learning database.
- Summarize previous ratings and repeated positive/negative comment terms into local preference context for later Ollama scoring.
- Enter an optional per-edit **External Promt** in the setup panel; Ollama combines it with saved ratings, comments, and previous prompts when selecting scenes.
- Save a reviewed render to a chosen location or regenerate the edit using saved ratings and an optional global prompt.
- Scan all source videos or limit the AI scan to the first 30 videos.
- Choose a 9:16, 16:9, 1:1, or 4:5 output size.
- Set a 15-, 30-, or 60-second duration, or use the full soundtrack duration.
- Optionally mute source-video audio and apply FFmpeg's `deshake` filter.
- Render H.264 video using NVIDIA NVENC at 60 FPS, with AAC soundtrack audio.

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
2. Choose a soundtrack (`.mp3`, `.wav`, `.aac`, or `.m4a`) and a reference video (`.mp4`, `.mov`, `.mkv`, or `.avi`).
3. Choose an output folder.
4. Select a timing mode:
   - **Reference**: follows scene-duration patterns detected in the reference video.
   - **Beat**: uses detected gaps between music beats.
   - **Hybrid**: combines reference durations with nearby beat timings.
5. Set the duration preset, output aspect ratio, and optional audio-muting and stabilization settings.
6. Enable or disable Ollama ranking. When enabled, choose an installed model and whether to scan all videos. The limited scan checks up to the first 30 videos.
7. Optionally enter an **External Promt** in the setup panel to describe this edit. The prompt is combined with learned ratings, comments, and previous edit prompts. Entering a prompt automatically uses the selected Ollama model for scene scoring, even if the ranking checkbox is off.
8. Start processing and follow progress in the application log. After rendering, FFmpeg checks that the output can be decoded and flags long near-black or frozen intervals and missing audio/video streams. These are review warnings (a dark or still scene can be intentional), not an automatic claim that every visual glitch has been found. The report is shown with the preview and stored in `Memory Learning/memory_learning.db`. A successful render is saved as an `AI_Render_<width>x<height>_60FPS_<timestamp>.mp4` file in the selected output folder.
9. Review the embedded Windows-compatible preview (a `_preview.wmv` sidecar with the rendered soundtrack). Playback starts automatically and pauses at each scene end; choose 1–5 stars, optionally add a comment, and confirm to continue. Use **Skip** to go straight to the completion panel or **Replay** to restart the scene review. Ratings and the current external prompt are stored locally in `Memory Learning/memory_learning.db`.
10. After the final scene, use **Save Video** to copy the original MP4 render to a chosen file path, or **Regenerate Edit** to run the pipeline again with learned ratings and an optional global prompt. Ollama scoring uses up to five prior positive/negative examples, previous external prompts and a summary of comment preferences; visually similar clips also receive a preference-based score adjustment.

### Notes

- Candidate segments are sampled at randomized positions distributed from the beginning through the end of each source video. With Ollama ranking enabled, the best-rated samples are selected and kept in chronological order within each source file; without it, samples are chosen across the timeline without AI ranking.
- The soundtrack is mapped as the output audio, and the current FFmpeg command maps only the concatenated video and soundtrack audio. The mute-source-audio option does not change that mapping in the current implementation.
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

AI Local Video Editor Studio Pro adalah aplikasi desktop untuk menyusun klip video bahan menjadi video yang diselaraskan dengan musik. Aplikasi menganalisis ketukan audio, pola durasi adegan pada video referensi, dapat memberi peringkat klip dengan model vision Ollama lokal, lalu merender hasil menggunakan FFmpeg.

### Fitur

- Memilih folder video bahan, soundtrack, video referensi, dan folder hasil.
- Menentukan timing potongan berdasarkan video referensi, beat musik, atau gabungan keduanya.
- Memberi peringkat klip secara opsional dengan menilai satu frame dari setiap video menggunakan model vision Ollama.
- Mengambil sampel klip secara acak-tersebar dari seluruh durasi video bahan, memberi peringkat adegan secara opsional dengan Ollama, dan menggunakan timestamp sumbernya—bukan selalu mengambil bagian awal.
- Meninjau hasil render di dalam aplikasi; playback berhenti pada batas setiap adegan untuk rating 1–5 bintang dan komentar opsional.
- Membuat preview WMV yang kompatibel dengan Windows dan berisi audio soundtrack, serta tombol Lewati ke panel akhir dan Putar Ulang.
- Menyimpan rating dan perceptual hash ringkas ke `Memory Learning/memory_learning.db`; rating sebelumnya digunakan untuk mempersonalisasi skor klip berikutnya.
- Menjalankan pemeriksaan decode/kualitas FFmpeg setelah render; menandai bagian hitam/beku yang panjang atau stream yang hilang, lalu menyimpan laporan ke database pembelajaran.
- Merangkum rating lama dan istilah komentar positif/negatif berulang sebagai konteks preferensi untuk penilaian Ollama berikutnya.
- Memasukkan **External Promt** opsional untuk tiap edit dari panel pengaturan; Ollama menggabungkannya dengan rating, komentar, dan prompt edit sebelumnya saat memilih adegan.
- Menyimpan hasil render ke lokasi pilihan atau membuat ulang edit dengan rating tersimpan dan prompt global opsional.
- Memindai semua video bahan atau membatasi pemindaian AI pada 30 video pertama.
- Memilih ukuran output 9:16, 16:9, 1:1, atau 4:5.
- Mengatur durasi 15, 30, atau 60 detik, atau mengikuti durasi penuh soundtrack.
- Mematikan audio dari video bahan dan mengaktifkan filter `deshake` FFmpeg secara opsional.
- Merender video H.264 menggunakan NVIDIA NVENC pada 60 FPS, dengan audio soundtrack AAC.

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
2. Pilih soundtrack (`.mp3`, `.wav`, `.aac`, atau `.m4a`) dan video referensi (`.mp4`, `.mov`, `.mkv`, atau `.avi`).
3. Pilih folder output.
4. Pilih mode timing:
   - **Reference**: mengikuti pola durasi adegan yang terdeteksi pada video referensi.
   - **Beat**: menggunakan jarak waktu antar-beat musik yang terdeteksi.
   - **Hybrid**: menggabungkan durasi referensi dengan timing beat terdekat.
5. Atur preset durasi, rasio output, serta opsi mute audio dan stabilisasi.
6. Aktifkan atau nonaktifkan pemeringkatan Ollama. Jika aktif, pilih model yang sudah terpasang dan tentukan apakah semua video akan dipindai. Pemindaian terbatas memeriksa maksimal 30 video pertama.
7. Opsional, isi **External Promt** pada panel pengaturan untuk menjelaskan edit ini. Prompt digabungkan dengan rating, komentar, dan prompt sebelumnya yang dipelajari. Jika prompt diisi, model Ollama yang dipilih akan digunakan untuk menilai adegan meskipun checkbox pemeringkatan tidak aktif.
8. Mulai proses dan pantau progres melalui log aplikasi. Setelah render, FFmpeg memeriksa apakah hasil dapat di-decode dan menandai bagian near-black/beku yang panjang serta stream audio/video yang hilang. Temuan ini adalah peringatan untuk ditinjau (adegan gelap atau diam bisa saja disengaja), bukan jaminan semua glitch visual ditemukan. Laporan ditampilkan bersama preview dan disimpan ke `Memory Learning/memory_learning.db`. Hasil render disimpan sebagai file `AI_Render_<lebar>x<tinggi>_60FPS_<timestamp>.mp4` di folder output.
9. Tinjau preview Windows-compatible (file pendamping `_preview.wmv` dengan soundtrack hasil render). Playback berjalan otomatis lalu berhenti pada akhir setiap adegan; pilih rating 1–5 bintang, tambahkan komentar jika perlu, lalu konfirmasi untuk melanjutkan. Gunakan **Skip** untuk langsung ke panel akhir atau **Replay** untuk mengulang review dari awal. Rating dan external prompt saat ini disimpan secara lokal ke `Memory Learning/memory_learning.db`.
10. Setelah adegan terakhir, gunakan **Save Video** untuk menyalin hasil render MP4 asli ke lokasi pilihan, atau **Regenerate Edit** untuk menjalankan pipeline kembali dengan rating yang dipelajari dan prompt global opsional. Penilaian Ollama memakai hingga lima contoh rating positif/negatif, prompt eksternal sebelumnya, serta ringkasan preferensi dari komentar; klip yang mirip secara visual juga mendapat penyesuaian skor.

### Catatan

- Kandidat klip diambil dari posisi acak-tersebar mulai awal sampai akhir setiap video bahan. Jika pemeringkatan Ollama aktif, sampel dengan skor terbaik dipilih dan dijaga urutan waktunya dalam setiap file sumber; jika tidak, sampel dipilih dari berbagai waktu tanpa pemeringkatan AI.
- Soundtrack dipetakan sebagai audio output, dan perintah FFmpeg saat ini hanya memetakan video gabungan serta audio soundtrack. Opsi mute audio bahan belum mengubah pemetaan tersebut pada implementasi saat ini.
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
