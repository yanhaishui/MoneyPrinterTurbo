import os
import random
import time
from typing import List
from urllib.parse import urlencode

import jwt
import requests
from loguru import logger
from moviepy.video.io.VideoFileClip import VideoFileClip

try:
    from volcenginesdkarkruntime import Ark as VolcengineArk
except ImportError:
    VolcengineArk = None

from app.config import config
from app.models.schema import MaterialInfo, VideoAspect, VideoConcatMode
from app.utils import utils

requested_count = 0


def get_api_key(cfg_key: str):
    api_keys = config.app.get(cfg_key)
    if not api_keys:
        raise ValueError(
            f"\n\n##### {cfg_key} is not set #####\n\nPlease set it in the config.toml file: {config.config_file}\n\n"
            f"{utils.to_json(config.app)}"
        )

    # if only one key is provided, return it
    if isinstance(api_keys, str):
        return api_keys

    global requested_count
    requested_count += 1
    return api_keys[requested_count % len(api_keys)]


def search_videos_pexels(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)
    video_orientation = aspect.name
    video_width, video_height = aspect.to_resolution()
    api_key = get_api_key("pexels_api_keys")
    headers = {
        "Authorization": api_key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    }
    # Build URL
    params = {"query": search_term, "per_page": 20, "orientation": video_orientation}
    query_url = f"https://api.pexels.com/videos/search?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url,
            headers=headers,
            proxies=config.proxy,
            verify=False,
            timeout=(30, 60),
        )
        response = r.json()
        video_items = []
        if "videos" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items
        videos = response["videos"]
        # loop through each video in the result
        for v in videos:
            duration = v["duration"]
            # check if video has desired minimum duration
            if duration < minimum_duration:
                continue
            video_files = v["video_files"]
            # loop through each url to determine the best quality
            for video in video_files:
                w = int(video["width"])
                h = int(video["height"])
                if w == video_width and h == video_height:
                    item = MaterialInfo()
                    item.provider = "pexels"
                    item.url = video["link"]
                    item.duration = duration
                    video_items.append(item)
                    break
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def search_videos_pixabay(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)

    video_width, video_height = aspect.to_resolution()

    api_key = get_api_key("pixabay_api_keys")
    # Build URL
    params = {
        "q": search_term,
        "video_type": "all",  # Accepted values: "all", "film", "animation"
        "per_page": 50,
        "key": api_key,
    }
    query_url = f"https://pixabay.com/api/videos/?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url, proxies=config.proxy, verify=False, timeout=(30, 60)
        )
        response = r.json()
        video_items = []
        if "hits" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items
        videos = response["hits"]
        # loop through each video in the result
        for v in videos:
            duration = v["duration"]
            # check if video has desired minimum duration
            if duration < minimum_duration:
                continue
            video_files = v["videos"]
            # loop through each url to determine the best quality
            for video_type in video_files:
                video = video_files[video_type]
                w = int(video["width"])
                # h = int(video["height"])
                if w >= video_width:
                    item = MaterialInfo()
                    item.provider = "pixabay"
                    item.url = video["url"]
                    item.duration = duration
                    video_items.append(item)
                    break
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def save_video(video_url: str, save_dir: str = "") -> str:
    if not save_dir:
        save_dir = utils.storage_dir("cache_videos")

    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    url_without_query = video_url.split("?")[0]
    url_hash = utils.md5(url_without_query)
    video_id = f"vid-{url_hash}"
    video_path = f"{save_dir}/{video_id}.mp4"

    # if video already exists, return the path
    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        logger.info(f"video already exists: {video_path}")
        return video_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    # if video does not exist, download it
    with open(video_path, "wb") as f:
        f.write(
            requests.get(
                video_url,
                headers=headers,
                proxies=config.proxy,
                verify=False,
                timeout=(60, 240),
            ).content
        )

    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        try:
            clip = VideoFileClip(video_path)
            duration = clip.duration
            fps = clip.fps
            clip.close()
            if duration > 0 and fps > 0:
                return video_path
        except Exception as e:
            try:
                os.remove(video_path)
            except Exception:
                pass
            logger.warning(f"invalid video file: {video_path} => {str(e)}")
    return ""


def _generate_kling_jwt(access_key: str, secret_key: str) -> str:
    """生成可灵 API JWT Token（HS256，有效期30分钟）"""
    now = int(time.time())
    payload = {
        "iss": access_key,
        "iat": now,
        "nbf": now - 5,
        "exp": now + 1800,
    }
    token = jwt.encode(
        payload,
        secret_key,
        algorithm="HS256",
        headers={"alg": "HS256", "typ": "JWT"},
    )
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def generate_videos_kling(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    """调用可灵官方 API 根据关键词生成视频，阻塞等待完成后返回 MaterialInfo 列表"""
    access_key = config.app.get("kling_access_key", "")
    secret_key = config.app.get("kling_secret_key", "")
    if not access_key or not secret_key:
        raise ValueError("kling_access_key or kling_secret_key is not set in config.toml")

    aspect = VideoAspect(video_aspect)
    w, h = aspect.to_resolution()
    if h > w:
        aspect_ratio = "9:16"
    elif w > h:
        aspect_ratio = "16:9"
    else:
        aspect_ratio = "1:1"

    model_name = config.app.get("kling_model_name", "kling-v1")
    duration = str(config.app.get("kling_duration", "5"))

    token = _generate_kling_jwt(access_key, secret_key)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "model_name": model_name,
        "prompt": search_term,
        "duration": duration,
        "mode": "std",
        "aspect_ratio": aspect_ratio,
    }

    try:
        resp = requests.post(
            "https://api.klingai.com/v1/videos/text2video",
            headers=headers,
            json=payload,
            proxies=config.proxy,
            verify=False,
            timeout=(30, 60),
        )
        result = resp.json()
    except Exception as e:
        logger.error(f"kling create task request failed: {e}")
        return []

    if result.get("code") != 0:
        logger.error(f"kling create task failed: {result}")
        return []

    task_id = result["data"]["task_id"]
    logger.info(f"kling task created: {task_id}, prompt: '{search_term}', waiting...")

    for i in range(60):  # 最多等待 5 分钟
        time.sleep(5)
        try:
            token = _generate_kling_jwt(access_key, secret_key)
            headers["Authorization"] = f"Bearer {token}"
            status_resp = requests.get(
                f"https://api.klingai.com/v1/videos/text2video/{task_id}",
                headers=headers,
                proxies=config.proxy,
                verify=False,
                timeout=(30, 60),
            )
            status = status_resp.json()
        except Exception as e:
            logger.warning(f"kling poll request failed: {e}, retrying...")
            continue

        task_status = status.get("data", {}).get("task_status", "")
        logger.info(f"kling task {task_id} status: {task_status} ({(i+1)*5}s elapsed)")

        if task_status == "succeed":
            videos = status["data"]["task_result"]["videos"]
            items = []
            for v in videos:
                item = MaterialInfo()
                item.provider = "kling"
                item.url = v["url"]
                item.duration = int(float(v.get("duration", duration)))
                items.append(item)
            logger.success(f"kling task {task_id} completed, {len(items)} videos generated")
            return items
        elif task_status == "failed":
            logger.error(f"kling task {task_id} failed: {status}")
            return []

    logger.error(f"kling task {task_id} timed out after 5 minutes")
    return []


def _download_kling_videos(
    task_id: str,
    search_terms: List[str],
    video_aspect: VideoAspect,
    audio_duration: float,
    max_clip_duration: int,
) -> List[str]:
    """逐个关键词调用可灵生成视频，下载到本地，达到所需时长后停止"""
    material_directory = config.app.get("material_directory", "").strip()
    if not material_directory or material_directory == "task":
        material_directory = utils.storage_dir("cache_videos")
    elif not os.path.isdir(material_directory):
        material_directory = utils.storage_dir("cache_videos")

    video_paths = []
    total_duration = 0.0

    for search_term in search_terms:
        if total_duration >= audio_duration:
            break
        logger.info(f"kling generating video for term: '{search_term}'")
        items = generate_videos_kling(search_term, max_clip_duration, video_aspect)
        for item in items:
            saved = save_video(video_url=item.url, save_dir=material_directory)
            if saved:
                video_paths.append(saved)
                total_duration += min(max_clip_duration, item.duration)
                logger.info(f"kling video saved: {saved}, total duration: {total_duration}s")
            if total_duration >= audio_duration:
                break

    logger.success(f"kling downloaded {len(video_paths)} videos, total duration: {total_duration}s")
    return video_paths


def _generate_volcengine_video(prompt: str, model_name: str, api_key: str, duration: int = 5) -> str:
    """调用火山方舟 Seedance API 生成单个视频，返回视频 URL"""
    if VolcengineArk is None:
        raise RuntimeError("volcengine-python-sdk not installed. Run: pip install 'volcengine-python-sdk[ark]'")

    client = VolcengineArk(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=api_key,
    )

    # 在 prompt 中附加参数（不指定 duration，由模型默认决定）
    full_prompt = f"{prompt} --camerafixed false --watermark false"

    create_result = client.content_generation.tasks.create(
        model=model_name,
        content=[{"type": "text", "text": full_prompt}],
    )
    task_id = create_result.id
    logger.info(f"volcengine task created: {task_id}, prompt: '{prompt}'")

    for i in range(100):  # 最多等待 5 分钟
        time.sleep(3)
        get_result = client.content_generation.tasks.get(task_id=task_id)
        status = get_result.status
        logger.info(f"volcengine task {task_id} status: {status} ({(i+1)*3}s elapsed)")
        if status == "succeeded":
            # 兼容不同 SDK 版本的响应结构
            try:
                video_url = get_result.content.video_url
            except AttributeError:
                try:
                    video_url = get_result.content.videos[0].url
                except (AttributeError, IndexError):
                    video_url = str(get_result)
            logger.success(f"volcengine task {task_id} succeeded, url: {video_url}")
            return video_url
        elif status == "failed":
            logger.error(f"volcengine task {task_id} failed: {get_result.error}")
            return ""

    logger.error(f"volcengine task {task_id} timed out")
    return ""


def _save_video_permissive_ssl(video_url: str, save_dir: str) -> str:
    """使用 curl 下载视频，绕过 Python 3.13 SSL 严格模式问题（适用于火山方舟 TOS 预签名链接）"""
    import subprocess

    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    url_without_query = video_url.split("?")[0]
    url_hash = utils.md5(url_without_query)
    video_id = f"vid-{url_hash}"
    video_path = f"{save_dir}/{video_id}.mp4"

    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        logger.info(f"video already exists: {video_path}")
        return video_path

    try:
        result = subprocess.run(
            [
                "curl", "-L", "-k", "--silent", "--show-error",
                "--http1.1",          # 禁用 HTTP/2，避免 TLS EOF 问题
                "--max-time", "1800", # 单次超时 30 分钟
                "--connect-timeout", "30",
                "--retry", "3",       # 失败自动重试 3 次
                "--retry-delay", "5",
                "--retry-max-time", "1800",
                "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "-o", video_path,
                video_url,
            ],
            capture_output=True,
            text=True,
            timeout=1860,
        )
        if result.returncode != 0:
            raise RuntimeError(f"curl failed: {result.stderr}")

        if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
            logger.info(f"volcengine video downloaded: {video_path}")
            return video_path
        else:
            raise RuntimeError("downloaded file is empty")
    except Exception as e:
        logger.error(f"failed to download volcengine video: {e}")
        try:
            os.remove(video_path)
        except Exception:
            pass
    return ""


def _download_volcengine_videos(
    task_id: str,
    search_terms: List[str],
    video_aspect: VideoAspect,
    audio_duration: float,
    max_clip_duration: int,
) -> List[str]:
    """逐个关键词调用火山方舟生成视频，下载到本地，达到所需时长后停止"""
    api_key = config.app.get("volcengine_api_key", "")
    model_name = config.app.get("volcengine_model_name", "doubao-seedance-1-5-pro-251215")
    if not api_key:
        raise ValueError("volcengine_api_key is not set in config.toml")

    material_directory = config.app.get("material_directory", "").strip()
    if not material_directory or material_directory == "task":
        material_directory = utils.storage_dir("cache_videos")
    elif not os.path.isdir(material_directory):
        material_directory = utils.storage_dir("cache_videos")

    video_paths = []
    total_duration = 0.0

    for search_term in search_terms:
        if total_duration >= audio_duration:
            break
        logger.info(f"volcengine generating video for term: '{search_term}'")
        video_url = _generate_volcengine_video(
            prompt=search_term,
            model_name=model_name,
            api_key=api_key,
            duration=min(max_clip_duration, 10),
        )
        if video_url:
            saved = _save_video_permissive_ssl(video_url=video_url, save_dir=material_directory)
            if saved:
                video_paths.append(saved)
                total_duration += min(max_clip_duration, 5)
                logger.info(f"volcengine video saved: {saved}, total duration: {total_duration}s")
        if total_duration >= audio_duration:
            break

    logger.success(f"volcengine downloaded {len(video_paths)} videos, total duration: {total_duration}s")
    return video_paths


def download_videos(
    task_id: str,
    search_terms: List[str],
    source: str = "pexels",
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_contact_mode: VideoConcatMode = VideoConcatMode.random,
    audio_duration: float = 0.0,
    max_clip_duration: int = 5,
) -> List[str]:
    if source == "kling":
        return _download_kling_videos(
            task_id, search_terms, video_aspect, audio_duration, max_clip_duration
        )
    if source == "volcengine":
        return _download_volcengine_videos(
            task_id, search_terms, video_aspect, audio_duration, max_clip_duration
        )

    valid_video_items = []
    valid_video_urls = []
    found_duration = 0.0
    search_videos = search_videos_pexels
    if source == "pixabay":
        search_videos = search_videos_pixabay

    for search_term in search_terms:
        video_items = search_videos(
            search_term=search_term,
            minimum_duration=max_clip_duration,
            video_aspect=video_aspect,
        )
        logger.info(f"found {len(video_items)} videos for '{search_term}'")

        for item in video_items:
            if item.url not in valid_video_urls:
                valid_video_items.append(item)
                valid_video_urls.append(item.url)
                found_duration += item.duration

    logger.info(
        f"found total videos: {len(valid_video_items)}, required duration: {audio_duration} seconds, found duration: {found_duration} seconds"
    )
    video_paths = []

    material_directory = config.app.get("material_directory", "").strip()
    if material_directory == "task":
        material_directory = utils.task_dir(task_id)
    elif material_directory and not os.path.isdir(material_directory):
        material_directory = ""

    if video_contact_mode.value == VideoConcatMode.random.value:
        random.shuffle(valid_video_items)

    total_duration = 0.0
    for item in valid_video_items:
        try:
            logger.info(f"downloading video: {item.url}")
            saved_video_path = save_video(
                video_url=item.url, save_dir=material_directory
            )
            if saved_video_path:
                logger.info(f"video saved: {saved_video_path}")
                video_paths.append(saved_video_path)
                seconds = min(max_clip_duration, item.duration)
                total_duration += seconds
                if total_duration > audio_duration:
                    logger.info(
                        f"total duration of downloaded videos: {total_duration} seconds, skip downloading more"
                    )
                    break
        except Exception as e:
            logger.error(f"failed to download video: {utils.to_json(item)} => {str(e)}")
    logger.success(f"downloaded {len(video_paths)} videos")
    return video_paths


if __name__ == "__main__":
    download_videos(
        "test123", ["Money Exchange Medium"], audio_duration=100, source="pixabay"
    )
