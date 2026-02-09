from __future__ import annotations

# rag_service/rag_service/core/video_analysis.py

import os
import json
import base64
import subprocess
import tempfile
import shutil
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

try:
    import requests
except ImportError:
    raise ImportError("requests library is required. Install with: pip install requests")

try:
    import cv2
    import numpy as np
except ImportError:
    raise ImportError("opencv-python and numpy are required. Install with: pip install opencv-python numpy")

@runtime_checkable
class LangChainManagerLike(Protocol):
    """Minimal interface needed by VideoSubmissionAnalyzer.

    This keeps `video_analysis.py` importable for local testing without `frappe`.
    """

    llm_provider: Any

    def get_universal_template(self) -> Any: ...
    def get_default_response_format(self) -> Dict: ...
    def format_objectives(self, objectives: List[Dict]) -> str: ...
    def format_rubrics(self, rubrics: Any) -> str: ...
    def clean_json_response(self, response: str) -> str: ...
    def validate_feedback_structure(self, feedback: Dict, expected_format: Dict) -> Dict: ...
    def create_error_feedback(self, assignment_context: Optional[Dict] = None) -> Dict: ...


class VideoSubmissionAnalyzer:
    """Analyzes video submissions by extracting key frames and using vision LLM"""
    
    def __init__(self, langchain_manager: LangChainManagerLike):
        """
        Initialize the video analyzer with a LangChainManager instance.
        
        Args:
            langchain_manager: LangChainManager instance for LLM operations
        """
        self.langchain_manager = langchain_manager
        self.blur_threshold = 60  # Laplacian variance threshold for blur detection
        
    def frame_to_data_url(self, frame_path: str) -> str:
        """
        Convert a frame image file to a base64 data URL.
        
        Args:
            frame_path: Path to the frame image file
            
        Returns:
            Data URL string in format: data:image/jpeg;base64,...
        """
        try:
            with open(frame_path, 'rb') as f:
                image_bytes = f.read()
            
            base64_encoded = base64.b64encode(image_bytes).decode('utf-8')
            return f"data:image/jpeg;base64,{base64_encoded}"
        except Exception as e:
            print(f"Error converting frame to data URL: {str(e)}")
            raise
    
    def download_video(self, video_url: str, temp_dir: str) -> str:
        """
        Download video from URL to a temporary file.
        
        Args:
            video_url: URL of the video to download
            temp_dir: Temporary directory to save the video
            
        Returns:
            Path to the downloaded video file
            
        Raises:
            Exception: If download fails
        """
        try:
            print(f"Downloading video from: {video_url}")
            response = requests.get(video_url, stream=True, timeout=300)
            response.raise_for_status()
            
            video_path = os.path.join(temp_dir, "video.mp4")
            with open(video_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            print(f"Video downloaded to: {video_path}")
            return video_path
        except Exception as e:
            error_msg = f"Error downloading video: {str(e)}"
            print(f"Error: {error_msg}")
            raise Exception(error_msg)
    
    def check_ffmpeg(self) -> bool:
        """
        Check if ffmpeg is available in the system.
        
        Returns:
            True if ffmpeg is available, False otherwise
        """
        try:
            subprocess.run(
                ['ffmpeg', '-version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    def extract_evenly_spaced_frames(self, video_path: str, output_dir: str, num_frames: int = 10) -> List[str]:
        """
        Extract evenly spaced frames from video using ffmpeg.
        
        Args:
            video_path: Path to the video file
            output_dir: Directory to save extracted frames
            num_frames: Number of frames to extract (default: 10)
            
        Returns:
            List of paths to extracted frame files
        """
        try:
            print(f"Extracting {num_frames} evenly spaced frames...")
            
            # Get video duration using ffprobe
            try:
                result = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=True,
                    timeout=30
                )
                duration = float(result.stdout.strip())
            except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
                print(f"Warning: Could not get video duration with ffprobe: {str(e)}")
                print("Attempting to extract frames without duration info...")
                # Fallback: extract frames at fixed intervals
                duration = 10.0  # Assume 10 seconds if we can't determine
            
            # Calculate frame intervals
            if num_frames <= 1:
                intervals = [duration / 2]
            else:
                intervals = [duration * i / (num_frames - 1) for i in range(num_frames)]
            
            frame_paths = []
            for i, timestamp in enumerate(intervals):
                frame_path = os.path.join(output_dir, f"frame_even_{i:03d}.jpg")
                try:
                    subprocess.run(
                        ['ffmpeg', '-i', video_path, '-ss', str(timestamp),
                         '-vframes', '1', '-q:v', '2', '-y', frame_path],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                        timeout=30
                    )
                    if os.path.exists(frame_path):
                        frame_paths.append(frame_path)
                        print(f"Extracted frame at {timestamp:.2f}s: {frame_path}")
                except subprocess.TimeoutExpired:
                    print(f"Timeout extracting frame at {timestamp:.2f}s")
                except subprocess.CalledProcessError as e:
                    print(f"Error extracting frame at {timestamp:.2f}s: {str(e)}")
            
            if not frame_paths:
                raise Exception("Failed to extract any frames from video")
            
            return frame_paths
        except subprocess.CalledProcessError as e:
            error_msg = f"Error extracting frames with ffmpeg: {str(e)}"
            print(f"Error: {error_msg}")
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error extracting frames: {str(e)}"
            print(f"Error: {error_msg}")
            raise Exception(error_msg)
    
    def score_blur(self, frame_path: str) -> float:
        """
        Calculate blur score for a frame using Laplacian variance.
        
        Args:
            frame_path: Path to the frame image
            
        Returns:
            Blur score (Laplacian variance). Higher values indicate sharper images.
        """
        try:
            image = cv2.imread(frame_path)
            if image is None:
                return 0.0
            
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            return float(laplacian_var)
        except Exception as e:
            print(f"Error scoring blur for {frame_path}: {str(e)}")
            return 0.0
    
    def extract_sharp_frames(self, video_path: str, output_dir: str, num_frames: int = 3) -> List[Tuple[str, float]]:
        """
        Extract frames with highest sharpness scores.
        
        Args:
            video_path: Path to the video file
            output_dir: Directory to save extracted frames
            num_frames: Number of sharp frames to extract (default: 3)
            
        Returns:
            List of tuples (frame_path, blur_score) sorted by score descending
        """
        try:
            print(f"Extracting {num_frames} sharpest frames...")
            
            # Extract more candidate frames for sharpness analysis
            candidate_count = max(20, num_frames * 5)
            temp_frames = self.extract_evenly_spaced_frames(video_path, output_dir, candidate_count)
            
            # Score all candidate frames
            scored_frames = []
            for frame_path in temp_frames:
                score = self.score_blur(frame_path)
                scored_frames.append((frame_path, score))
                print(f"Frame {os.path.basename(frame_path)}: blur score = {score:.2f}")
            
            # Sort by score (highest first) and take top N
            scored_frames.sort(key=lambda x: x[1], reverse=True)
            top_frames = scored_frames[:num_frames]
            
            # Rename top frames to indicate they're sharp
            sharp_frames = []
            for i, (frame_path, score) in enumerate(top_frames):
                new_path = os.path.join(output_dir, f"frame_sharp_{i:03d}.jpg")
                shutil.move(frame_path, new_path)
                sharp_frames.append((new_path, score))
            
            # Clean up remaining candidate frames
            for frame_path, _ in scored_frames[num_frames:]:
                if os.path.exists(frame_path):
                    os.remove(frame_path)
            
            return sharp_frames
        except Exception as e:
            error_msg = f"Error extracting sharp frames: {str(e)}"
            print(f"Error: {error_msg}")
            raise Exception(error_msg)
    
    def select_best_frames(self, evenly_spaced: List[str], sharp_frames: List[Tuple[str, float]], max_frames: int = 6) -> List[Tuple[str, float]]:
        """
        Select best frames from evenly spaced and sharp frames.
        
        Args:
            evenly_spaced: List of paths to evenly spaced frames
            sharp_frames: List of tuples (frame_path, blur_score) for sharp frames
            max_frames: Maximum number of frames to select (default: 6)
            
        Returns:
            List of tuples (frame_path, blur_score) for selected frames
        """
        try:
            # Score evenly spaced frames
            scored_evenly = []
            for frame_path in evenly_spaced:
                score = self.score_blur(frame_path)
                scored_evenly.append((frame_path, score))
            
            # Combine and deduplicate (by path)
            all_frames = {}
            for frame_path, score in scored_evenly + sharp_frames:
                all_frames[frame_path] = score
            
            # Sort by score and take top frames
            sorted_frames = sorted(all_frames.items(), key=lambda x: x[1], reverse=True)
            
            # Mix: take some evenly spaced (for coverage) and some sharp (for quality)
            selected = []
            
            # Prioritize sharp frames, then fill with evenly spaced
            for frame_path, score in sorted_frames:
                if len(selected) >= max_frames:
                    break
                
                # Check if this is a sharp frame
                is_sharp = any(frame_path == path for path, _ in sharp_frames)
                if is_sharp and len(selected) < max_frames:
                    selected.append((frame_path, score))
                elif not is_sharp and len(selected) < max_frames:
                    # Add evenly spaced frame if we don't have enough
                    selected.append((frame_path, score))
            
            # Ensure we have a good mix
            if len(selected) < max_frames and len(sorted_frames) > len(selected):
                # Add more from sorted list
                for frame_path, score in sorted_frames:
                    if len(selected) >= max_frames:
                        break
                    if (frame_path, score) not in selected:
                        selected.append((frame_path, score))
            
            print(f"Selected {len(selected)} frames for analysis")
            return selected[:max_frames]
        except Exception as e:
            error_msg = f"Error selecting best frames: {str(e)}"
            print(f"Error: {error_msg}")
            raise Exception(error_msg)
    
    async def call_llm_on_frame(self, frame_data_url: str, system_prompt: str, user_prompt: str) -> Dict:
        """
        Call LLM vision API on a single frame.
        
        Args:
            frame_data_url: Data URL of the frame image
            system_prompt: System prompt for the LLM
            user_prompt: User prompt for the LLM
            
        Returns:
            Parsed JSON feedback dictionary
        """
        try:
            # Format messages using the provider's format_messages method
            messages = self.langchain_manager.llm_provider.format_messages(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                image_url=frame_data_url
            )
            
            # Generate response
            raw_text = await self.langchain_manager.llm_provider.generate_with_vision(messages)
            
            # Clean and parse JSON
            cleaned_text = self.langchain_manager.clean_json_response(raw_text)
            feedback = json.loads(cleaned_text)
            
            return feedback
        except json.JSONDecodeError as e:
            print(f"JSON parse error for frame: {str(e)}")
            # Return empty dict, will be handled in aggregation
            return {}
        except Exception as e:
            print(f"Error calling LLM on frame: {str(e)}")
            return {}
    
    def aggregate_frame_results(self, frame_results: List[Dict], frame_info: List[Tuple[str, float]], expected_format: Dict) -> Dict:
        """
        Aggregate feedback results from multiple frames.
        
        Args:
            frame_results: List of feedback dictionaries from each frame
            frame_info: List of tuples (frame_path, blur_score) for frames used
            expected_format: Expected feedback format structure
            
        Returns:
            Aggregated feedback dictionary
        """
        try:
            # Filter out empty results
            valid_results = [r for r in frame_results if r and isinstance(r, dict)]
            
            if not valid_results:
                return {}
            
            aggregated = {}
            
            # Aggregate scalar fields (average)
            # Newer schemas may use `final_grade` instead of `grade_recommendation`.
            scalar_fields = ["grade_recommendation", "final_grade"]
            for field in scalar_fields:
                if field in expected_format:
                    values = []
                    for result in valid_results:
                        if field in result:
                            try:
                                val = float(result[field])
                                values.append(val)
                            except (ValueError, TypeError):
                                pass
                    
                    if values:
                        avg_value = sum(values) / len(values)
                        # Clamp to 0-100
                        aggregated[field] = max(0, min(100, avg_value))
                    else:
                        aggregated[field] = expected_format.get(field, 0)
            
            # Aggregate text fields (choose longest non-fallback)
            text_fields = ["overall_feedback", "overall_feedback_translated", "encouragement"]
            for field in text_fields:
                if field in expected_format:
                    candidates = [r.get(field, "") for r in valid_results if r.get(field)]
                    # Filter out fallback messages
                    non_fallback = [c for c in candidates if "technical" not in c.lower() and "error" not in c.lower() and "issue" not in c.lower()]
                    if non_fallback:
                        # Choose longest
                        aggregated[field] = max(non_fallback, key=len)
                    elif candidates:
                        aggregated[field] = candidates[0]
                    else:
                        aggregated[field] = expected_format.get(field, "")
            
            # Aggregate list fields (merge unique items)
            list_fields = ["strengths", "areas_for_improvement", "learning_objectives_feedback"]
            for field in list_fields:
                if field in expected_format:
                    all_items = []
                    for result in valid_results:
                        if field in result and isinstance(result[field], list):
                            all_items.extend(result[field])
                    
                    # Deduplicate while preserving order
                    seen = set()
                    unique_items = []
                    for item in all_items:
                        item_str = str(item).lower().strip()
                        if item_str and item_str not in seen:
                            seen.add(item_str)
                            unique_items.append(item)
                    
                    aggregated[field] = unique_items if unique_items else expected_format.get(field, [])

            # Aggregate rubric_evaluations (if schema expects it)
            if "rubric_evaluations" in expected_format:
                # Map skill -> {grade_values: [...], observations: [...]}
                by_skill: Dict[str, Dict[str, Any]] = {}
                for result in valid_results:
                    rubrics = result.get("rubric_evaluations")
                    if not isinstance(rubrics, list):
                        continue
                    for item in rubrics:
                        if not isinstance(item, dict):
                            continue
                        skill = item.get("skill") or item.get("Skill") or "Unknown"
                        skill_key = str(skill).strip() or "Unknown"
                        grade_val = item.get("grade_value")
                        obs = item.get("observation")

                        bucket = by_skill.setdefault(skill_key, {"grade_values": [], "observations": []})
                        try:
                            if grade_val is not None:
                                bucket["grade_values"].append(float(grade_val))
                        except (ValueError, TypeError):
                            pass
                        if isinstance(obs, str) and obs.strip():
                            bucket["observations"].append(obs.strip())

                rubric_out: List[Dict[str, Any]] = []
                for skill_key, bucket in by_skill.items():
                    grades = bucket["grade_values"]
                    avg_grade = round(sum(grades) / len(grades), 2) if grades else expected_format.get("rubric_evaluations", [{}])[0].get("grade_value", 0)
                    # Clamp 0-5 if that's the rubric scale, otherwise leave as-is
                    avg_grade = max(0.0, min(5.0, float(avg_grade)))
                    # Merge observations unique
                    seen_obs = set()
                    merged_obs: List[str] = []
                    for o in bucket["observations"]:
                        key = o.lower()
                        if key not in seen_obs:
                            seen_obs.add(key)
                            merged_obs.append(o)
                    rubric_out.append(
                        {
                            "skill": skill_key,
                            "grade_value": avg_grade,
                            "observation": " | ".join(merged_obs) if merged_obs else "No specific evidence provided",
                        }
                    )

                # Preserve expected key casing if provided (some templates use "Skill")
                aggregated["rubric_evaluations"] = rubric_out if rubric_out else expected_format.get("rubric_evaluations", [])
            
            # Add video-specific metadata
            blur_scores = [score for _, score in frame_info]
            aggregated["video_evidence"] = [
                {
                    "frame": os.path.basename(path),
                    "frame_index": i,
                    "blur_score": score
                }
                for i, (path, score) in enumerate(frame_info)
            ]
            
            aggregated["video_quality"] = {
                "frames_extracted": len(frame_info),
                "frames_used": len(frame_info),
                "blur_scores": blur_scores,
                "quality_flag": self._determine_quality_flag(blur_scores, len(frame_info))
            }
            
            # Calculate confidence
            aggregated["confidence"] = self._calculate_confidence(
                blur_scores,
                aggregated.get("video_quality", {}).get("quality_flag", "ok"),
                len(valid_results)
            )
            
            return aggregated
        except Exception as e:
            print(f"Error aggregating frame results: {str(e)}")
            return {}
    
    def _determine_quality_flag(self, blur_scores: List[float], num_frames: int) -> str:
        """
        Determine quality flag based on blur scores and frame count.
        
        Args:
            blur_scores: List of blur scores
            num_frames: Number of frames
            
        Returns:
            Quality flag: "ok", "blurry", or "insufficient_frames"
        """
        if num_frames < 3:
            return "insufficient_frames"
        
        blurry_count = sum(1 for score in blur_scores if score < self.blur_threshold)
        if blurry_count > len(blur_scores) / 2:
            return "blurry"
        
        return "ok"
    
    def _calculate_confidence(self, blur_scores: List[float], quality_flag: str, valid_results: int) -> float:
        """
        Calculate confidence score (0.0-1.0) based on quality metrics.
        
        Args:
            blur_scores: List of blur scores
            quality_flag: Quality flag from _determine_quality_flag
            valid_results: Number of valid LLM results
            
        Returns:
            Confidence score between 0.0 and 1.0
        """
        if quality_flag == "insufficient_frames":
            return 0.2
        elif quality_flag == "blurry":
            return 0.4
        
        # Base confidence from frame count
        frame_confidence = min(1.0, valid_results / 6.0)
        
        # Adjust based on blur scores
        avg_blur = sum(blur_scores) / len(blur_scores) if blur_scores else 0
        blur_confidence = min(1.0, avg_blur / 100.0)  # Normalize to 0-1
        
        # Combine confidences
        confidence = (frame_confidence * 0.6 + blur_confidence * 0.4)
        return max(0.0, min(1.0, confidence))
    
    def create_video_fallback_feedback(self, assignment_context: Dict, expected_format: Dict, reason: str = "unclear_video") -> Dict:
        """
        Create fallback feedback for video submissions that cannot be graded reliably.
        
        Args:
            assignment_context: Assignment context dictionary
            expected_format: Expected feedback format
            reason: Reason for fallback ("unclear_video", "insufficient_frames", etc.)
            
        Returns:
            Fallback feedback dictionary
        """
        assignment_name = assignment_context["assignment"].get("name", "this assignment")
        
        if reason == "insufficient_frames":
            message = f"I was unable to extract enough clear frames from your video submission for {assignment_name}. Please ensure your video clearly shows your artwork and try resubmitting."
        elif reason == "blurry":
            message = f"The frames extracted from your video submission for {assignment_name} were too blurry to evaluate reliably. Please ensure your video is in focus and clearly shows your artwork, then resubmit."
        else:
            message = f"I encountered difficulty evaluating your video submission for {assignment_name}. The artwork shown in the video frames was unclear. Please resubmit with a clearer video that shows your work."
        
        fallback = {}
        for field, default_value in expected_format.items():
            if field == "overall_feedback":
                fallback[field] = message
            elif field == "grade_recommendation":
                fallback[field] = 0
            elif isinstance(default_value, list):
                if "strength" in field:
                    fallback[field] = ["Unable to assess - video quality insufficient"]
                elif "improvement" in field:
                    fallback[field] = ["Please resubmit with a clearer video"]
                else:
                    fallback[field] = ["Unable to provide specific feedback due to video quality issues"]
            else:
                if field == "encouragement":
                    fallback[field] = "Please resubmit with a clearer video so we can properly evaluate your work!"
                else:
                    fallback[field] = "Video quality insufficient - please resubmit"
        
        # Add video quality metadata
        fallback["video_quality"] = {
            "frames_extracted": 0,
            "frames_used": 0,
            "blur_scores": [],
            "quality_flag": reason
        }
        fallback["confidence"] = 0.1
        fallback["video_evidence"] = []
        
        return fallback
    
    async def analyze_video_submission(self, assignment_context: Dict, video_url: str, submission_id: str) -> Tuple[Dict, str]:
        """
        Analyze a video submission by extracting frames and using vision LLM.
        
        Args:
            assignment_context: Assignment context dictionary
            video_url: URL of the video submission
            submission_id: ID of the submission
            
        Returns:
            Tuple of (feedback_dict, template_used_string)
        """
        temp_dir = None
        temp_video_path = None
        
        try:
            print("\n=== Starting Video Submission Analysis ===")
            
            # Check ffmpeg availability
            if not self.check_ffmpeg():
                raise Exception("ffmpeg is not available. Please install ffmpeg to process video submissions.")
            
            # Create temp directory for this submission
            temp_dir = tempfile.mkdtemp(prefix=f"video_analysis_{submission_id}_")
            print(f"Created temp directory: {temp_dir}")
            
            # Download video
            temp_video_path = self.download_video(video_url, temp_dir)
            
            # Get template (same as image analysis)
            template = self.langchain_manager.get_universal_template()
            print("Template loaded successfully")
            
            # Get expected response format
            try:
                if hasattr(template, 'response_format') and template.response_format:
                    expected_format = json.loads(template.response_format)
                    print("Using template-defined response format")
                else:
                    expected_format = self.langchain_manager.get_default_response_format()
                    print("Using default response format")
            except json.JSONDecodeError:
                expected_format = self.langchain_manager.get_default_response_format()
                print("Failed to parse template response format, using default")
            
            # Format learning objectives + rubric criteria (if present in schema/context)
            learning_objectives = self.langchain_manager.format_objectives(
                assignment_context.get("learning_objectives", [])
            )
            rubric_criteria = ""
            try:
                rubrics = assignment_context.get("assignment", {}).get("rubrics", "")
                if rubrics and hasattr(self.langchain_manager, "format_rubrics"):
                    rubric_criteria = self.langchain_manager.format_rubrics(rubrics)
            except Exception:
                rubric_criteria = ""
            
            # Format user prompt (add video-specific instruction)
            user_prompt_vars = {
                "assignment_name": assignment_context["assignment"].get("name", ""),
                "assignment_description": assignment_context["assignment"].get("description", ""),
                # Newer contexts may use `subject` instead of `course_vertical`
                "course_vertical": assignment_context.get("course_vertical", assignment_context.get("subject", "General")),
                "assignment_type": assignment_context["assignment"].get("type", "Practical"),
                "learning_objectives": learning_objectives,
                "rubric_criteria": rubric_criteria,
                "Language": assignment_context.get("student", {}).get("language", "English"),
                "Grade_Level": assignment_context.get("student", {}).get("grade", "1"),
            }
            
            formatted_user_prompt = template.user_prompt
            for key, value in user_prompt_vars.items():
                placeholder = "{" + key + "}"
                if placeholder in formatted_user_prompt:
                    formatted_user_prompt = formatted_user_prompt.replace(placeholder, str(value))
            
            # Add video-specific instruction
            video_instruction = "\n\nThis submission is a VIDEO. Evaluate the ARTWORK shown in the frames. Ignore cinematography. If the artwork is unclear in frames, return low confidence and request resubmission."
            formatted_user_prompt += video_instruction
            
            system_prompt = template.system_prompt
            
            # Extract frames
            print("\nExtracting frames from video...")
            evenly_spaced = self.extract_evenly_spaced_frames(temp_video_path, temp_dir, num_frames=10)
            sharp_frames_with_scores = self.extract_sharp_frames(temp_video_path, temp_dir, num_frames=3)
            sharp_frames = [path for path, _ in sharp_frames_with_scores]
            frames_extracted_total = len(set(evenly_spaced) | set(sharp_frames))
            
            # Select best frames
            selected_frames = self.select_best_frames(evenly_spaced, sharp_frames_with_scores, max_frames=6)
            
            # Quality gate check
            blur_scores = [score for _, score in selected_frames]
            quality_flag = self._determine_quality_flag(blur_scores, len(selected_frames))
            
            if quality_flag != "ok" or len(selected_frames) < 3:
                print(f"Video quality insufficient: {quality_flag}, frames: {len(selected_frames)}")
                template_used = template.name if hasattr(template, 'name') else "Built-in Universal Template"
                fallback = self.create_video_fallback_feedback(assignment_context, expected_format, quality_flag)
                fallback["video_quality"] = {
                    "frames_extracted": frames_extracted_total,
                    "frames_used": 0,
                    "blur_scores": blur_scores,
                    "quality_flag": quality_flag,
                }
                fallback["confidence"] = self._calculate_confidence(blur_scores, quality_flag, 0)
                fallback = self.langchain_manager.validate_feedback_structure(fallback, expected_format)
                # Add plagiarism output
                fallback["plagiarism_output"] = {
                    "is_plagiarized": False,
                    "is_ai_generated": False,
                    "match_type": "original",
                    "plagiarism_source": "none",
                    "similarity_score": 0.0,
                    "ai_detection_source": "none",
                    "ai_confidence": 0.0,
                    "similar_sources": []
                }
                return fallback, template_used
            
            # Call LLM on each frame
            print(f"\nCalling LLM on {len(selected_frames)} frames...")
            frame_results = []
            for i, (frame_path, blur_score) in enumerate(selected_frames):
                print(f"Processing frame {i+1}/{len(selected_frames)}: {os.path.basename(frame_path)} (blur: {blur_score:.2f})")
                frame_data_url = self.frame_to_data_url(frame_path)
                frame_feedback = await self.call_llm_on_frame(frame_data_url, system_prompt, formatted_user_prompt)
                if frame_feedback:
                    frame_results.append(frame_feedback)
            
            # Aggregate results
            print("\nAggregating results from frames...")
            aggregated = self.aggregate_frame_results(frame_results, selected_frames, expected_format)
            if isinstance(aggregated, dict):
                aggregated.setdefault("video_quality", {})
                if isinstance(aggregated["video_quality"], dict):
                    aggregated["video_quality"]["frames_extracted"] = frames_extracted_total
            
            # Validate structure
            aggregated = self.langchain_manager.validate_feedback_structure(aggregated, expected_format)
            
            # Add plagiarism output (same as image analysis)
            aggregated["plagiarism_output"] = {
                "is_plagiarized": False,
                "is_ai_generated": False,
                "match_type": "original",
                "plagiarism_source": "none",
                "similarity_score": 0.0,
                "ai_detection_source": "none",
                "ai_confidence": 0.0,
                "similar_sources": []
            }
            
            # Get template name
            try:
                if hasattr(template, 'name'):
                    template_used = template.name
                else:
                    template_used = "Built-in Universal Template"
            except Exception:
                template_used = "Built-in Universal Template"
            
            print("\n=== Video Analysis Completed Successfully ===")
            return aggregated, template_used
            
        except Exception as e:
            error_msg = f"Error analyzing video submission {submission_id}: {str(e)}"
            print(f"\nError: {error_msg}")
            
            # Try to get template for error response
            try:
                template = self.langchain_manager.get_universal_template()
                expected_format = json.loads(template.response_format) if hasattr(template, 'response_format') and template.response_format else self.langchain_manager.get_default_response_format()
            except:
                expected_format = self.langchain_manager.get_default_response_format()
            
            # LangChainManager signature differs across branches; support both.
            try:
                error_feedback = self.langchain_manager.create_error_feedback(assignment_context)
            except TypeError:
                error_feedback = self.langchain_manager.create_error_feedback()
            error_feedback = self.langchain_manager.validate_feedback_structure(error_feedback, expected_format)
            error_feedback["plagiarism_output"] = {
                "is_plagiarized": False,
                "is_ai_generated": False,
                "match_type": "original",
                "plagiarism_source": "none",
                "similarity_score": 0.0,
                "ai_detection_source": "none",
                "ai_confidence": 0.0,
                "similar_sources": []
            }
            
            template_used = "Built-in Universal Template for Error"
            return error_feedback, template_used
            
        finally:
            # Clean up temp files
            if temp_dir and os.path.exists(temp_dir):
                print(f"Cleaning up temp directory: {temp_dir}")
                shutil.rmtree(temp_dir, ignore_errors=True)

