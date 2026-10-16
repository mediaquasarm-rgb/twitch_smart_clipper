import json, os, re, sys, threading, queue, subprocess, tempfile, wave, math, shutil
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np
import yt_dlp
import imageio_ffmpeg

APP = 'Twitch Smart Clipper'
DATA = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'TwitchSmartClipper'
DATA.mkdir(parents=True, exist_ok=True)
PREF = DATA / 'preferences.json'
DEFAULT = {'min_length':25,'count':8,'webcam_x':0.70,'webcam_y':0.02,'webcam_w':0.28,'webcam_h':0.32,'likes':[],'dislikes':[],'notes':[]}

def load():
    try:
        return {**DEFAULT, **json.loads(PREF.read_text(encoding='utf-8'))}
    except Exception: return dict(DEFAULT)

def save(p): PREF.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')

def ffmpeg(): return imageio_ffmpeg.get_ffmpeg_exe()

def run(cmd, log):
    log('Выполняется: '+str(cmd[0]))
    kw={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',**kw)
    if proc.returncode: raise RuntimeError(proc.stderr[-1800:])
    return proc

def download(url,folder,log):
    log('Загрузка записи Twitch (может занять время)…')
    opts={'outtmpl':str(folder/'stream.%(ext)s'),'format':'best[height<=720]/best','noplaylist':True,'quiet':True,'no_warnings':True,'merge_output_format':'mp4'}
    with yt_dlp.YoutubeDL(opts) as y:
        info=y.extract_info(url,download=True)
        path=Path(y.prepare_filename(info))
    candidates=sorted(folder.glob('stream.*'),key=lambda p:p.stat().st_size,reverse=True)
    if not candidates: raise RuntimeError('Не удалось найти скачанную запись')
    return candidates[0]

def energy_windows(video,log):
    log('Анализ громкости и динамики звука…')
    with tempfile.TemporaryDirectory() as t:
        wav=Path(t)/'audio.wav'
        run([ffmpeg(),'-y','-i',str(video),'-vn','-ac','1','-ar','8000','-acodec','pcm_s16le',str(wav)],log)
        with wave.open(str(wav),'rb') as w:
            raw=w.readframes(w.getnframes()); rate=w.getframerate()
        a=np.frombuffer(raw,dtype=np.int16).astype(np.float32)/32768
    step=rate*2
    count=len(a)//step
    if count<10: raise RuntimeError('Слишком короткое видео')
    a=a[:count*step].reshape(count,step)
    rms=np.sqrt(np.mean(a*a,axis=1)+1e-9)
    diff=np.abs(np.diff(rms,prepend=rms[0]))
    # Adaptive scoring: excitement is not equivalent to humor.
    z=lambda v:(v-np.median(v))/(np.std(v)+1e-6)
    return 2*np.arange(count),0.65*z(rms)+0.35*z(diff)

def select_clips(times,scores,seconds,count,likes,dislikes):
    # Human feedback affects preference for intensity of reactions; no false claim of semantic understanding.
    favor = np.mean([x.get('energy',0) for x in likes[-40:]]) if likes else 0
    avoid = np.mean([x.get('energy',0) for x in dislikes[-40:]]) if dislikes else 0
    adjusted=np.array(scores,dtype=float)
    if likes or dislikes:
        adjusted += .25*(np.abs(scores-avoid)-np.abs(scores-favor))
    ranked=np.argsort(adjusted)[::-1]
    picked=[]
    for i in ranked:
        start=max(0,float(times[i])-seconds*.4)
        if all(abs(start-s)>seconds*.9 for s,_,_ in picked):
            picked.append((start,seconds,float(scores[i])))
        if len(picked)>=count: break
    return sorted(picked)

def make_clip(source,out,start,duration,p,log):
    # Top: webcam crop, bottom: main scene. User can tune webcam location in Preferences.
    x,y,w,h=[float(p[k]) for k in ('webcam_x','webcam_y','webcam_w','webcam_h')]
    # Crop expressions are evaluated in source coordinate space; pad preserves 9:16.
    filt=(f'[0:v]split=2[a][b];'
          f'[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=720:600:force_original_aspect_ratio=increase,crop=720:600[top];'
          f'[b]scale=720:680:force_original_aspect_ratio=increase,crop=720:680[bottom];'
          f'[top][bottom]vstack=inputs=2[v]')
    common=[ffmpeg(),'-y','-ss',f'{start:.2f}','-i',str(source),'-t',str(duration),'-filter_complex',filt,'-map','[v]','-map','0:a?','-c:a','aac','-b:a','128k','-movflags','+faststart']
    try:
        run(common+['-c:v','h264_nvenc','-preset','p4','-b:v','4M',str(out)],log)
    except RuntimeError:
        log('NVENC недоступен, переключение на программное кодирование…')
        run(common+['-c:v','libx264','-preset','veryfast','-crf','23',str(out)],log)

class App:
    def __init__(self,root):
        self.root=root; root.title(APP);root.geometry('790x690');self.q=queue.Queue();self.p=load();self.busy=False;self.clips=[];self.scores=[]
        ttk.Label(root,text='Twitch Smart Clipper',font=('Segoe UI',18,'bold')).pack(anchor='w',padx=16,pady=(12,4))
        ttk.Label(root,text='Автоматическая нарезка TikTok: стример сверху, видео снизу').pack(anchor='w',padx=16)
        line=ttk.Frame(root);line.pack(fill='x',padx=16,pady=12)
        self.url=tk.StringVar(value='https://www.twitch.tv/videos/2900708121')
        ttk.Entry(line,textvariable=self.url).pack(side='left',fill='x',expand=True)
        ttk.Button(line,text='Выбрать MP4',command=self.choose).pack(side='left',padx=5)
        self.path=tk.StringVar(value=str(Path.home()/'Videos'/'TwitchClips'))
        line2=ttk.Frame(root);line2.pack(fill='x',padx=16)
        ttk.Label(line2,text='Клипы:').pack(side='left');ttk.Entry(line2,textvariable=self.path).pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(line2,text='Папка',command=self.folder).pack(side='left')
        opts=ttk.Frame(root);opts.pack(fill='x',padx=16,pady=8)
        ttk.Button(opts,text='Нарезать',command=self.start).pack(side='left')
        ttk.Button(opts,text='Настроить камеру',command=self.camera).pack(side='left',padx=8)
        ttk.Button(opts,text='Открыть папку',command=self.open_folder).pack(side='left')
        ttk.Label(root,text='Готовые клипы (выбери клип и оцени его):').pack(anchor='w',padx=16)
        self.list=tk.Listbox(root,height=6);self.list.pack(fill='x',padx=16,pady=4)
        f=ttk.Frame(root);f.pack(fill='x',padx=16)
        ttk.Button(f,text='👍 Нравится',command=lambda:self.feedback(True)).pack(side='left')
        ttk.Button(f,text='👎 Не нравится',command=lambda:self.feedback(False)).pack(side='left',padx=8)
        ttk.Label(root,text='Чат правок и предпочтений (правила сохраняются локально):').pack(anchor='w',padx=16,pady=(12,2))
        self.chat=tk.Text(root,height=9,wrap='word');self.chat.pack(fill='both',expand=True,padx=16)
        inp=ttk.Frame(root);inp.pack(fill='x',padx=16,pady=6)
        self.msg=tk.StringVar();ttk.Entry(inp,textvariable=self.msg).pack(side='left',fill='x',expand=True)
        ttk.Button(inp,text='Отправить',command=self.chat_send).pack(side='left',padx=5)
        ttk.Label(root,text='Примеры: «делай 12 клипов», «длина 35 секунд», «не люблю крики», «камера слева сверху»',foreground='gray').pack(anchor='w',padx=16)
        self.chat.insert('end','Помощник: Я сохраняю твои предпочтения. Я пока не понимаю смысл шуток: выбираю кандидатов по динамике звука.\n')
        root.after(150,self.poll)
    def choose(self):
        p=filedialog.askopenfilename(filetypes=[('Видео','*.mp4 *.mkv *.webm *.mov'),('Все файлы','*.*')]);
        if p:self.url.set(p)
    def folder(self):
        p=filedialog.askdirectory();
        if p:self.path.set(p)
    def open_folder(self):
        d=Path(self.path.get());d.mkdir(parents=True,exist_ok=True)
        if os.name=='nt':os.startfile(d)
    def log(self,s):self.q.put(('log',s))
    def start(self):
        if self.busy:return
        self.busy=True;self.clips=[];self.list.delete(0,'end');self.log('Запуск…')
        threading.Thread(target=self.worker,daemon=True).start()
    def worker(self):
        try:
            output=Path(self.path.get());output.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory() as t:
                src=self.url.get().strip()
                if src.startswith(('https://','http://')):
                    source=download(src,Path(t),self.log)
                else:
                    source=Path(src)
                    if not source.is_file():raise RuntimeError('Выбери файл или вставь ссылку Twitch')
                times,scores=energy_windows(source,self.log)
                p=load();selected=select_clips(times,scores,int(p['min_length']),int(p['count']),p['likes'],p['dislikes'])
                for n,(start,dur,score) in enumerate(selected,1):
                    out=output/f'clip_{n:02d}_{int(start)}s.mp4'
                    self.log(f'Клип {n}/{len(selected)}: {start:.0f}–{start+dur:.0f} сек')
                    make_clip(source,out,start,dur,p,self.log)
                    self.q.put(('clip',(str(out),score)))
            self.log('Готово! Оцени клипы, чтобы настроить будущий отбор.')
        except Exception as e:self.log('ОШИБКА: '+str(e))
        finally:self.q.put(('done',None))
    def poll(self):
        try:
            while True:
                typ,val=self.q.get_nowait()
                if typ=='log':self.chat.insert('end','Система: '+val+'\n');self.chat.see('end')
                elif typ=='clip':self.clips.append(val);self.list.insert('end',Path(val[0]).name)
                elif typ=='done':self.busy=False
        except queue.Empty:pass
        self.root.after(150,self.poll)
    def feedback(self,good):
        ix=self.list.curselection()
        if not ix:messagebox.showinfo(APP,'Сначала выбери клип в списке');return
        path,score=self.clips[ix[0]];p=load();key='likes' if good else 'dislikes'
        p[key].append({'clip':Path(path).name,'energy':score});save(p)
        self.chat.insert('end',f'Помощник: Оценка сохранена: {Path(path).name}. Учту при следующем ранжировании.\n')
    def chat_send(self):
        msg=self.msg.get().strip();self.msg.set('')
        if not msg:return
        self.chat.insert('end','Ты: '+msg+'\n');p=load();reply=[]
        m=re.search(r'(?:делай|сделай|хочу|нужно)\s+(\d+)\s+клип',msg,re.I)
        if m:p['count']=max(1,min(30,int(m.group(1))));reply.append(f'Количество клипов: {p["count"]}')
        m=re.search(r'(?:длина|длительность|по)\s+(\d+)\s+сек',msg,re.I)
        if m:p['min_length']=max(10,min(90,int(m.group(1))));reply.append(f'Длительность: {p["min_length"]} сек')
        if 'камера слева' in msg.lower():p.update(webcam_x=.02,webcam_y=.02);reply.append('Камера: слева сверху')
        if 'камера справа' in msg.lower():p.update(webcam_x=.70,webcam_y=.02);reply.append('Камера: справа сверху')
        p['notes'].append(msg);save(p)
        if not reply:reply=['Пожелание сохранено в истории. Пока свободный текст не управляет монтажом автоматически; используй оценки 👍/👎 и поддерживаемые команды.']
        self.chat.insert('end','Помощник: '+'; '.join(reply)+'\n');self.chat.see('end')
    def camera(self):
        win=tk.Toplevel(self.root);win.title('Положение веб-камеры');win.geometry('360x260')
        entries={}
        for key,label in [('webcam_x','X (0–1)'),('webcam_y','Y (0–1)'),('webcam_w','Ширина (0–1)'),('webcam_h','Высота (0–1)')]:
            row=ttk.Frame(win);row.pack(fill='x',padx=12,pady=7)
            ttk.Label(row,text=label,width=18).pack(side='left')
            v=tk.StringVar(value=str(self.p[key]));ttk.Entry(row,textvariable=v).pack(side='left');entries[key]=v
        def apply():
            try:
                v={k:float(s.get()) for k,s in entries.items()}
                if any(not 0<=n<=1 for n in v.values()) or v['webcam_w']<=0 or v['webcam_h']<=0 or v['webcam_x']+v['webcam_w']>1 or v['webcam_y']+v['webcam_h']>1:raise ValueError()
                self.p.update(v);save(self.p);win.destroy()
            except ValueError:messagebox.showerror(APP,'Введите доли 0–1; область должна помещаться в кадре')
        ttk.Button(win,text='Сохранить',command=apply).pack(pady=8)

if __name__=='__main__':
    root=tk.Tk();App(root);root.mainloop()
