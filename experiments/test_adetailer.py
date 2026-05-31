"""
Automated Eye Quality Enhancement for AI-Generated Anime Images
Uses face detection + Stable Diffusion inpainting to fix low-quality eyes

Requirements:
- Stable Diffusion WebUI running with API enabled
- mediapipe for face detection
- PIL for image processing
"""

import requests
import base64
import io
import cv2
import numpy as np
from pathlib import Path
from PIL import Image
import mediapipe as mp
import json
from ultralytics import YOLO

# Set-ExecutionPolicy -ExecutionPolicy RemoteSign
# .\venv\Scripts\activate 
# cd "D:\3D Objects\sd.webui\webui"
# python launch.py --api

# Checkpoint Model - Uses whatever you have loaded (IMPORTANT!)
# VAE - If you have a custom VAE selected, it will use it
# Clip Skip - Uses your current clip skip setting
# Extensions - Some extensions might interfere:

# Configuration
SD_API_URL = "http://127.0.0.1:7860"  # Default SD WebUI address
INPUT_FOLDER = r"H:\VAMA Project\test_images\input"
OUTPUT_FOLDER = r"H:\VAMA Project\test_images\output"
MASK_FOLDER = r"H:\VAMA Project\test_images\masks"  # For debugging

# Custom YOLO model for eye detection (set to None to use MediaPipe only)
YOLO_MODEL_PATH = r"H:\VAMA Project\test_images\fullEyesDetection_v10\full_eyes_detect_v1.pt"

# Inpainting settings
INPAINT_CONFIG = {
    "prompt": "masterpiece, best quality, highly detailed anime eyes, sharp clear pupils, beautiful iris detail, perfect symmetry, extremely detailed, 8k, ultra sharp focus",
    "negative_prompt": "blurry, low quality, distorted, malformed eyes, asymmetric, watermark",
    "sampler_name": "DPM++ 2M",
    "steps": 50,
    "cfg_scale": 7.0,
    "denoising_strength": 0.2,  # Lower = preserve more original, higher = more change
    "inpaint_full_res": True,
    "inpaint_full_res_padding": 32,
    "inpainting_fill": 1,  # 1 = original, 0 = fill, 2 = latent noise
    "width": 512,
    "height": 512,
}

class EyeInpainter:
    def __init__(self, yolo_model_path=None):
        """Initialize face detection"""
        # Load custom YOLO model if provided
        self.yolo_model = None
        if yolo_model_path and Path(yolo_model_path).exists():
            try:
                print(f"Loading YOLO model: {yolo_model_path}")
                self.yolo_model = YOLO(yolo_model_path)
                print("✓ YOLO model loaded successfully")
            except Exception as e:
                print(f"Failed to load YOLO model: {e}")
                print("Falling back to MediaPipe")
        
        # MediaPipe with relaxed settings for anime (fallback)
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=5,
            refine_landmarks=True,
            min_detection_confidence=0.1
        )
        
        self.mp_face_detection = mp.solutions.face_detection
        self.face_detection = self.mp_face_detection.FaceDetection(
            model_selection=1,
            min_detection_confidence=0.1
        )
        
        # Eye landmark indices (MediaPipe 468 landmarks)
        self.LEFT_EYE_INDICES = [33, 133, 160, 159, 158, 157, 173, 144, 145, 153, 154, 155, 163, 7]
        self.RIGHT_EYE_INDICES = [362, 263, 387, 386, 385, 384, 398, 373, 374, 380, 381, 382, 390, 249]
        
    def detect_eyes(self, image_np):
        """
        Detect eye regions in image - tries YOLO first, then falls back to MediaPipe
        Returns list of (x, y, w, h) bounding boxes for each eye
        """
        h, w = image_np.shape[:2]
        eye_regions = []
        
        # Method 1: Custom YOLO model (if loaded)
        if self.yolo_model is not None:
            try:
                results = self.yolo_model(image_np, verbose=False)
                if len(results) > 0:
                    boxes = results[0].boxes
                    for box in boxes:
                        # Get bounding box coordinates
                        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                        confidence = float(box.conf[0])
                        
                        # Only use high confidence detections
                        if confidence > 0.3:
                            # YOLO detects whole eye region, add it directly
                            eye_regions.append({
                                'box': (x1, y1, x2, y2),
                                'side': 'detected',  # Single detection
                                'confidence': confidence
                            })
                    
                    if eye_regions:
                        return eye_regions
            except Exception as e:
                print(f"  YOLO detection failed: {e}")
        
        # Method 2: MediaPipe face detection with estimated eye positions
        rgb_image = cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
        results = self.face_detection.process(rgb_image)
        if results.detections:
            for detection in results.detections:
                bbox = detection.location_data.relative_bounding_box
                x = int(bbox.xmin * w)
                y = int(bbox.ymin * h)
                face_w = int(bbox.width * w)
                face_h = int(bbox.height * h)
                
                # Anime-specific proportions
                eye_y = y + int(face_h * 0.32)
                eye_h = int(face_h * 0.22)
                
                # Left eye
                left_eye_x = x + int(face_w * 0.20)
                eye_w = int(face_w * 0.28)
                eye_regions.append({
                    'box': (max(0, left_eye_x), max(0, eye_y),
                           min(w, left_eye_x + eye_w), min(h, eye_y + eye_h)),
                    'side': 'left'
                })
                
                # Right eye
                right_eye_x = x + int(face_w * 0.52)
                eye_regions.append({
                    'box': (max(0, right_eye_x), max(0, eye_y),
                           min(w, right_eye_x + eye_w), min(h, eye_y + eye_h)),
                    'side': 'right'
                })
        
        # Method 3: Try face mesh for precise landmarks
        if not eye_regions:
            results = self.face_mesh.process(rgb_image)
            
            if results.multi_face_landmarks:
                for face_landmarks in results.multi_face_landmarks:
                    landmarks = face_landmarks.landmark
                    
                    # Get left eye region
                    left_eye_points = [(int(landmarks[idx].x * w), int(landmarks[idx].y * h)) 
                                      for idx in self.LEFT_EYE_INDICES]
                    x, y, w_box, h_box = cv2.boundingRect(np.array(left_eye_points))
                    padding = 20
                    eye_regions.append({
                        'box': (max(0, x-padding), max(0, y-padding), 
                               min(w, x+w_box+padding*2), min(h, y+h_box+padding*2)),
                        'side': 'left'
                    })
                    
                    # Get right eye region
                    right_eye_points = [(int(landmarks[idx].x * w), int(landmarks[idx].y * h)) 
                                       for idx in self.RIGHT_EYE_INDICES]
                    x, y, w_box, h_box = cv2.boundingRect(np.array(right_eye_points))
                    eye_regions.append({
                        'box': (max(0, x-padding), max(0, y-padding), 
                               min(w, x+w_box+padding*2), min(h, y+h_box+padding*2)),
                        'side': 'right'
                    })
        
        return eye_regions
    
    def create_mask(self, image_shape, eye_regions):
        """Create inpainting mask with white regions for eyes"""
        mask = np.zeros(image_shape[:2], dtype=np.uint8)
        
        for region in eye_regions:
            x1, y1, x2, y2 = region['box']
            # Create elliptical mask for more natural inpainting
            center = ((x1 + x2) // 2, (y1 + y2) // 2)
            axes = ((x2 - x1) // 2, (y2 - y1) // 2)
            cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)
        
        return mask
    
    def encode_image(self, image):
        """Convert PIL Image to base64 string"""
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    def inpaint_via_api(self, image_pil, mask_pil):
        """Send inpainting request to SD WebUI API"""
        payload = {
            "init_images": [self.encode_image(image_pil)],
            "mask": self.encode_image(mask_pil),
            "mask_blur": 4,
            "inpainting_mask_invert": 0,
            **INPAINT_CONFIG
        }
        
        try:
            response = requests.post(f"{SD_API_URL}/sdapi/v1/img2img", json=payload, timeout=300)
            response.raise_for_status()
            
            result = response.json()
            if 'images' in result and len(result['images']) > 0:
                # Decode base64 image
                img_data = base64.b64decode(result['images'][0])
                return Image.open(io.BytesIO(img_data))
            else:
                print("No image returned from API")
                return None
                
        except requests.exceptions.ConnectionError:
            print(f"ERROR: Cannot connect to Stable Diffusion API at {SD_API_URL}")
            print("Make sure SD WebUI is running with --api flag")
            return None
        except Exception as e:
            print(f"Error during inpainting: {e}")
            return None
    
    def process_image(self, image_path, save_mask=False):
        """Process single image: detect eyes and inpaint"""
        print(f"\nProcessing: {image_path.name}")
        
        # Load image
        image = cv2.imread(str(image_path))
        if image is None:
            print(f"  ERROR: Could not load image")
            return None
        
        # Detect eyes
        print(f"  Detecting eyes...")
        eye_regions = self.detect_eyes(image)
        
        if not eye_regions:
            print(f"  WARNING: No eyes detected, skipping")
            return None
        
        print(f"  Found {len(eye_regions)} eye regions")
        
        # Create mask
        mask = self.create_mask(image.shape, eye_regions)
        
        # Save mask for debugging
        if save_mask:
            mask_path = Path(MASK_FOLDER) / f"{image_path.stem}_mask.png"
            cv2.imwrite(str(mask_path), mask)
            print(f"  Saved mask to: {mask_path}")
        
        # Convert to PIL
        image_pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        mask_pil = Image.fromarray(mask).convert('RGB')
        
        # Inpaint via API
        print(f"  Inpainting eyes...")
        result = self.inpaint_via_api(image_pil, mask_pil)
        
        if result:
            print(f"  ✓ Successfully processed")
        else:
            print(f"  ✗ Inpainting failed")
            
        return result
    
    def batch_process(self, input_folder, output_folder, save_masks=False):
        """Process all images in folder"""
        input_path = Path(input_folder)
        output_path = Path(output_folder)
        output_path.mkdir(parents=True, exist_ok=True)
        
        if save_masks:
            Path(MASK_FOLDER).mkdir(parents=True, exist_ok=True)
        
        # Get all image files
        image_extensions = ['.jpg', '.jpeg', '.png', '.webp']
        image_files = [f for f in input_path.iterdir() 
                      if f.suffix.lower() in image_extensions]
        
        if not image_files:
            print(f"No images found in {input_folder}")
            return
        
        print(f"\nFound {len(image_files)} images to process")
        print(f"Input folder: {input_folder}")
        print(f"Output folder: {output_folder}")
        print("="*60)
        
        success_count = 0
        failed_count = 0
        
        for i, image_file in enumerate(image_files, 1):
            print(f"\n[{i}/{len(image_files)}]", end=" ")
            
            result = self.process_image(image_file, save_mask=save_masks)
            
            if result:
                output_file = output_path / f"{image_file.stem}_fixed{image_file.suffix}"
                result.save(output_file)
                success_count += 1
            else:
                failed_count += 1
        
        print("\n" + "="*60)
        print(f"Processing complete!")
        print(f"  ✓ Success: {success_count}")
        print(f"  ✗ Failed: {failed_count}")
        print(f"  Output: {output_folder}")

def check_sd_api():
    """Check if SD WebUI API is accessible"""
    try:
        response = requests.get(f"{SD_API_URL}/sdapi/v1/sd-models", timeout=5)
        if response.status_code == 200:
            models = response.json()
            print(f"✓ SD WebUI API is running")
            print(f"  Available models: {len(models)}")
            if models:
                print(f"  Current model: {models[0].get('model_name', 'Unknown')}")
            return True
    except:
        pass
    
    print(f"✗ SD WebUI API not accessible at {SD_API_URL}")
    print(f"  Start SD WebUI with: python launch.py --api")
    return False

if __name__ == "__main__":
    print("="*60)
    print("Automated Eye Quality Enhancement")
    print("="*60)
    
    # Check API availability
    if not check_sd_api():
        print("\nPlease start Stable Diffusion WebUI first!")
        exit(1)
    
    # Initialize processor
    processor = EyeInpainter(yolo_model_path=YOLO_MODEL_PATH)
    
    # Process all images
    processor.batch_process(
        input_folder=INPUT_FOLDER,
        output_folder=OUTPUT_FOLDER,
        save_masks=True  # Set False to skip mask debugging
    )
