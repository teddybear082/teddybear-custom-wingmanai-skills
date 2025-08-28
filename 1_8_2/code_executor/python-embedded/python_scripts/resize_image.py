from PIL import Image

# Path to the original image
image_path = "C:\\Users\\Willi\\AppData\\Roaming\\ShipBit\\WingmanAI\\1_6_0\\skills\\youtube_assistant\\logo.png"
# Path to save the resized image
output_path = "C:\\Users\\Willi\\AppData\\Roaming\\ShipBit\\WingmanAI\\1_6_0\\skills\\youtube_assistant\\logo2.png"

# Open the original image
original_image = Image.open(image_path)
# Resize the image to 128x128 pixels
resized_image = original_image.resize((128, 128))

# Save the resized image
resized_image.save(output_path)
print("Image resized and saved as logo2.png")